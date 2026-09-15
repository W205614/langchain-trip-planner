"""Agent photo capability helpers. Public HTTP routing is owned by Java."""

import ipaddress
import base64
import math
import re
import logging
import socket
import threading
import time
from html import escape
from collections import OrderedDict
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from fastapi.responses import Response

from .amap_service import get_amap_service
from .execution import deadline_var
from PIL import Image

logger = logging.getLogger(__name__)

# 高德个人开发者 key 有 QPS 限制(约3-5), 前端结果页会并发请求所有景点图片。
# 用信号量限制同时并发 + 最小间隔节流, 双保险防止 CUQPS_HAS_EXCEEDED_THE_LIMIT 超限。
_photo_semaphore = threading.BoundedSemaphore(3)
_PHOTO_MIN_INTERVAL = 0.4  # 高德图片调用最小间隔(秒), 0.4s/次 ≈ 2.5 QPS
_photo_last_call = 0.0
_photo_call_lock = threading.Lock()
_photo_url_cache: dict[str, tuple[float, Optional[str]]] = {}
_photo_url_cache_lock = threading.Lock()
_PHOTO_URL_CACHE_TTL_SECONDS = 60 * 60
_MAX_PROXY_IMAGE_BYTES = 5 * 1024 * 1024
_photo_deadline = ContextVar("photo_deadline", default=None)
_photo_records = OrderedDict()
_photo_results = OrderedDict()
_photo_failures = OrderedDict()


def _cache_get(cache, key):
    with _photo_url_cache_lock:
        item = cache.get(key)
        if item and item[0] > time.monotonic():
            cache.move_to_end(key)
            return item[1]
        cache.pop(key, None)
    return None


def _cache_put(cache, key, value, ttl, limit=256):
    with _photo_url_cache_lock:
        cache[key] = (time.monotonic() + ttl, value)
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)


def _photo_remaining():
    deadline = _photo_deadline.get()
    return max(0, deadline - time.monotonic()) if deadline is not None else 10.0


def _photo_lookup(key, lookup):
    cached = _cache_get(_photo_records, key)
    if cached is not None:
        return cached
    if not _photo_semaphore.acquire(timeout=_photo_remaining()):
        return {}
    try:
        global _photo_last_call
        with _photo_call_lock:
            wait = max(0, _photo_last_call + _PHOTO_MIN_INTERVAL - time.monotonic())
            if wait >= _photo_remaining():
                return {}
            time.sleep(wait)
            _photo_last_call = time.monotonic()
        result = lookup() or {}
        _cache_put(_photo_records, key, result, 3600 if result else 30)
        return result
    finally:
        _photo_semaphore.release()


def _photo_urls(detail):
    return list(dict.fromkeys(p["url"] for p in (detail.get("photos") or [])
                             if isinstance(p, dict) and isinstance(p.get("url"), str)))[:3]


def _try_photo_urls(detail):
    for url in _photo_urls(detail):
        if _photo_remaining() < 0.1:
            break
        if _cache_get(_photo_failures, url):
            continue
        image = _download_photo(url)
        if image:
            return image
        _cache_put(_photo_failures, url, True, 30)
    return None


def _normalized_name(value):
    return re.sub(r"[\s·・\-—()（）]", "", str(value or ""))


def _parent_name(name):
    # Only a named enclosing attraction, never strip arbitrary last characters.
    match = re.match(r"^(.{2,}?(?:景区|风景区|公园|博物院|博物馆|动物园|植物园|度假区))(.+)$", name)
    return match.group(1) if match else ""


def _same_city(detail, city):
    actual = detail.get("cityname") or detail.get("city")
    return isinstance(actual, str) and bool(city) and actual.removesuffix("市") == city.removesuffix("市")


def _nearby(first, second):
    try:
        lon1, lat1 = map(float, first["location"].split(","))
        lon2, lat2 = map(float, second["location"].split(","))
        if not all(math.isfinite(v) for v in (lon1, lat1, lon2, lat2)):
            return False
        if not (-180 <= lon1 <= 180 and -180 <= lon2 <= 180 and -90 <= lat1 <= 90 and -90 <= lat2 <= 90):
            return False
        distance = 6371000 * 2 * math.asin(min(1, math.sqrt(
            math.sin(math.radians(lat2-lat1)/2)**2 + math.cos(math.radians(lat1)) *
            math.cos(math.radians(lat2)) * math.sin(math.radians(lon2-lon1)/2)**2)))
        return distance <= 3000
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def _reference_photo(image, parent_name):
    content, mime = image
    # Embed raster bytes only: no untrusted SVG or external resources inside SVG.
    if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        return None
    encoded = base64.b64encode(content).decode("ascii")
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500" viewBox="0 0 800 500">
<image width="800" height="500" preserveAspectRatio="xMidYMid slice" href="data:{mime};base64,{encoded}"/>
<rect y="422" width="800" height="78" fill="#111827" fill-opacity="0.85"/>
<text x="24" y="451" fill="white" font-family="sans-serif" font-size="20">{escape(parent_name[:36])}</text>
<text x="24" y="480" fill="white" font-family="sans-serif" font-size="18">景区参考图（非该具体点位实拍）</text></svg>'''
    return svg.encode("utf-8"), "image/svg+xml"


def _verified_poi_photo(service, name, poi_id, city):
    detail = _photo_lookup("detail:" + poi_id, lambda: service.get_poi_detail(poi_id))
    if not isinstance(detail, dict) or (detail.get("id") and detail["id"] != poi_id):
        return None
    image = _try_photo_urls(detail)
    if image:
        return image
    # Fail closed when identity/location evidence is missing; do not use Bing's first result.
    parent = _parent_name(_normalized_name(detail.get("name") or name))
    if not parent or not _same_city(detail, city) or _photo_remaining() < 1:
        return None
    results = _photo_lookup("parent:" + city + ":" + parent,
        lambda: service._get("/v3/place/text", {"keywords": parent, "city": city,
            "citylimit": "true", "offset": 3, "extensions": "all"}))
    candidates = [p for p in (results.get("pois") or [])[:3] if isinstance(p, dict)
                  and _normalized_name(p.get("name")) == parent
                  and _same_city(p, city) and _nearby(detail, p) and p.get("id") != poi_id]
    # Ambiguous parent entities must not silently pick the first search hit.
    unique = {p.get("id"): p for p in candidates if p.get("id")}
    if len(unique) != 1:
        return None
    candidate = next(iter(unique.values()))
    image = _try_photo_urls(candidate)
    if not image and _photo_remaining() >= 1:
        extra = _photo_lookup("detail:" + candidate["id"], lambda: service.get_poi_detail(candidate["id"]))
        if isinstance(extra, dict) and extra.get("id") == candidate["id"]:
            image = _try_photo_urls(extra)
    return _reference_photo(image, parent) if image else None


def _rate_limited_photo_call(amap_service, name: str) -> Optional[str]:
    """带间隔节流的高德图片调用(线程安全)"""
    global _photo_last_call
    with _photo_call_lock:
        wait = _photo_last_call + _PHOTO_MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _photo_last_call = time.monotonic()
    return amap_service.get_poi_photo_by_name(name)










def _resolve_attraction_photo(name: str) -> Optional[str]:
    """解析景点图片地址，供元数据和同源图片代理共用。"""
    normalized_name = name.strip()
    now = time.monotonic()
    with _photo_url_cache_lock:
        cached = _photo_url_cache.get(normalized_name)
        if cached and cached[0] > now:
            return cached[1]

    # 高德POI图片 (国内图源); 信号量限并发 + 间隔节流限QPS, 防止CUQPS超限
    amap_service = get_amap_service()
    with _photo_semaphore:
        photo_url = _rate_limited_photo_call(amap_service, normalized_name)

    # 高德无图 → 必应图片兜底 (免费稳定, 返回 CDN 直链)
    if not photo_url:
        photo_url = _bing_image_fallback(normalized_name)

    with _photo_url_cache_lock:
        _photo_url_cache[normalized_name] = (now + (_PHOTO_URL_CACHE_TTL_SECONDS if photo_url else 30), photo_url)

    return photo_url


def _is_safe_remote_url(url: str) -> bool:
    """仅允许解析到公网地址的 HTTP(S) 图片地址，避免代理访问内网。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    try:
        ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
        if ips and all(ip.is_global for ip in ips):
            return True
        # Local proxy fake-IP DNS uses the benchmark range. Only the exact AMap
        # image endpoint is eligible; HTTPS still validates its hostname and every
        # redirect is checked again. Private/LAN targets remain forbidden.
        trusted_path = ((parsed.hostname == "store.is.autonavi.com" and parsed.path.startswith("/showpic/")) or
                        (parsed.hostname == "aos-cdn-image.amap.com" and parsed.path.startswith("/sns/")) or
                        (parsed.hostname == "aos-comment.amap.com" and (
                            re.match(r"^/[A-Za-z0-9]+/headerImg/", parsed.path) or
                            re.fullmatch(r"/[A-Za-z0-9]+/comment/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)", parsed.path))))
        return bool(ips) and parsed.scheme == "https" and trusted_path and port == 443 \
            and all(ip in ipaddress.ip_network("198.18.0.0/15") for ip in ips)
    except ValueError:
        return False


def _download_photo(url: str) -> tuple[bytes, str] | None:
    """下载受验证的公网图片；逐跳验证跳转并流式限制内容大小。"""
    try:
        parsed = urlparse(url)
        # AMap returns legacy HTTP URLs even though both official CDNs serve TLS.
        next_url = parsed._replace(scheme="https").geturl() if parsed.scheme == "http" and parsed.hostname in {
            "store.is.autonavi.com", "aos-cdn-image.amap.com", "aos-comment.amap.com"} and parsed.port in (None, 80) else url
        with httpx.Client(timeout=max(0.1, min(3.0, _photo_remaining())), follow_redirects=False) as client:
            for _ in range(4):
                if _photo_remaining() < 0.1:
                    return None
                if not _is_safe_remote_url(next_url):
                    logger.warning("景点图片代理拒绝非公网地址")
                    return None
                with client.stream("GET", next_url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            return None
                        next_url = urljoin(next_url, location)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if not content_type.startswith("image/") and content_type != "application/octet-stream":
                        logger.warning("景点图片代理收到非图片响应: %s", content_type)
                        return None
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > _MAX_PROXY_IMAGE_BYTES:
                        logger.warning("景点图片代理拒绝超大图片: %s bytes", content_length)
                        return None
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        if _photo_remaining() < 0.1:
                            return None
                        size += len(chunk)
                        if size > _MAX_PROXY_IMAGE_BYTES:
                            logger.warning("景点图片代理拒绝超大图片")
                            return None
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    if content_type == "application/octet-stream":
                        if content.startswith(b"\xff\xd8\xff"):
                            content_type = "image/jpeg"
                        elif content.startswith(b"\x89PNG\r\n\x1a\n"):
                            content_type = "image/png"
                        elif content[:6] in (b"GIF87a", b"GIF89a"):
                            content_type = "image/gif"
                        elif content[:4] == b"RIFF" and content[8:12] == b"WEBP":
                            content_type = "image/webp"
                        else:
                            return None
                    with Image.open(BytesIO(content)) as decoded:
                        if decoded.width * decoded.height > 24_000_000:
                            return None
                        content_type = {"JPEG": "image/jpeg", "PNG": "image/png",
                                        "WEBP": "image/webp", "GIF": "image/gif"}.get(decoded.format)
                        if not content_type:
                            return None
                        decoded.verify()
                    return content, content_type
        return None
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.info("下载景点图片失败: %s", exc)
        return None


def _photo_placeholder(name: str) -> Response:
    """返回可导出的同源 SVG 占位图，避免上游 CDN 波动造成页面/PDF 留白。"""
    safe_name = escape(name[:40])
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500" viewBox="0 0 800 500">
  <defs><linearGradient id="background" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#667eea"/><stop offset="1" stop-color="#764ba2"/></linearGradient></defs>
  <rect width="800" height="500" fill="url(#background)"/>
  <text x="400" y="230" text-anchor="middle" font-family="sans-serif" font-size="36" font-weight="700" fill="#fff">{safe_name}</text>
  <text x="400" y="285" text-anchor="middle" font-family="sans-serif" font-size="22" fill="#ede9fe">景点图片暂不可用</text>
</svg>'''
    return Response(
        content=svg.encode("utf-8"),
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )




def render_attraction_photo(name: str, poi_id: str = "", city: str = ""):
    """将已解析的景点图片作为同源图片返回，不接受任意 URL，避免开放代理。"""
    if poi_id:
        key = (poi_id, name, city)
        cached = _cache_get(_photo_results, key)
        if cached is not None:
            return Response(content=cached[0], media_type=cached[1], headers={"Cache-Control":
                "no-store" if cached[1] == "image/svg+xml" else "public, max-age=3600"})
        token = _photo_deadline.set(time.monotonic() + 24)
        upstream_deadline = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=24)
        previous_deadline = deadline_var.get()
        deadline_token = deadline_var.set(min(previous_deadline, upstream_deadline) if previous_deadline else upstream_deadline)
        try:
            image = _verified_poi_photo(get_amap_service(), name, poi_id, city)
            if image:
                # At most 16 * ~6.7 MB, rather than an unbounded in-memory image cache.
                _cache_put(_photo_results, key, image, 3600, limit=16)
                return Response(content=image[0], media_type=image[1], headers={"Cache-Control":
                    "no-store" if image[1] == "image/svg+xml" else "public, max-age=3600"})
        except Exception:
            logger.info("POI图片查询暂不可用")
        finally:
            _photo_deadline.reset(token)
            deadline_var.reset(deadline_token)
        placeholder = _photo_placeholder(name)
        _cache_put(_photo_results, key, (placeholder.body, "image/svg+xml"), 30, limit=16)
        return placeholder
    photo_url = _resolve_attraction_photo(f"{city} {name}".strip())
    if not photo_url:
        return _photo_placeholder(name)

    downloaded = _download_photo(photo_url)
    if not downloaded:
        return _photo_placeholder(name)

    content, content_type = downloaded
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


# 必应图片兜底: 简单抓取搜索结果中的 CDN 图 URL (免费稳定, 不依赖第三方key)
# 用缓存在内存中避免重复请求
_bing_cache: dict = {}
_bing_lock = threading.Lock()


def _bing_image_fallback(name: str) -> Optional[str]:
    """高德无图时, 用必应图片搜索兜底 (返回首个结果的 CDN 直链)"""
    cached = _bing_cache.get(name)
    if cached:
        return cached
    try:
        import re as _re
        import urllib.parse
        import urllib.request

        q = urllib.parse.quote(f"{name} 景点")
        # 必应图片搜索返回 HTML, 内含 murl (媒体直链)
        req = urllib.request.Request(
            f"https://www.bing.com/images/search?q={q}&qft=+filterui:photo-photo",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", errors="ignore")
        # 提取第一个 murl="..." 直链
        m = _re.search(r'murl&quot;:&quot;(https?:[^&"]+?)&quot;', html) \
            or _re.search(r'"murl":"(https?:[^"]+?)"', html)
        if not m:
            return None
        url = m.group(1).replace("\\/", "/")
        # 内存缓存 1 小时
        with _bing_lock:
            _bing_cache[name] = url
        return url
    except Exception as e:
        logger.debug(f"必应图片兜底失败 {name}: {e}")
        return None
