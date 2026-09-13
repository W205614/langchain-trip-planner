"""POI相关API路由"""

import ipaddress
import re
import logging
import socket
import threading
import time
from html import escape
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ...core.rate_limit import limiter
from ...services.amap_service import get_amap_service

router = APIRouter(prefix="/poi", tags=["POI"])

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


def _rate_limited_photo_call(amap_service, name: str) -> Optional[str]:
    """带间隔节流的高德图片调用(线程安全)"""
    global _photo_last_call
    with _photo_call_lock:
        wait = _photo_last_call + _PHOTO_MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _photo_last_call = time.monotonic()
    return amap_service.get_poi_photo_by_name(name)


class POIDetailResponse(BaseModel):
    """POI详情响应"""
    success: bool
    message: str
    data: Optional[dict] = None


@router.get(
    "/detail/{poi_id}",
    response_model=POIDetailResponse,
    summary="获取POI详情",
    description="根据POI ID获取详细信息,包括图片",
)
@limiter.limit("30/minute")
def get_poi_detail(request: Request, poi_id: str):
    """
    获取POI详情 (同步端点, 线程池执行, 不阻塞事件循环)

    Args:
        poi_id: POI ID

    Returns:
        POI详情响应
    """
    amap_service = get_amap_service()
    result = amap_service.get_poi_detail(poi_id)

    return POIDetailResponse(
        success=True,
        message="获取POI详情成功",
        data=result,
    )


@router.get(
    "/search",
    summary="搜索POI",
    description="根据关键词搜索POI",
)
@limiter.limit("30/minute")
def search_poi(request: Request, keywords: str, city: str = "北京"):
    """
    搜索POI (同步端点, 线程池执行, 不阻塞事件循环)

    Args:
        keywords: 搜索关键词
        city: 城市名称

    Returns:
        搜索结果
    """
    amap_service = get_amap_service()
    result = amap_service.search_poi(keywords, city)

    return {
        "success": True,
        "message": "搜索成功",
        "data": result,
    }


@router.get(
    "/photo",
    summary="获取景点图片",
    description="根据景点名称获取高德POI实景图(国内图源), 无图返回空由前端用占位图兜底",
)
@limiter.limit("30/minute")
def get_attraction_photo(request: Request, name: str = Query(min_length=1, max_length=100)):
    """
    获取景点图片

    优先取高德POI实景图(国内图源, 快且稳); 高德无图时退回必应图片搜索 (CDN直链)。
    两者都无则返回 null, 前端用占位图兜底。

    Args:
        name: 景点名称

    Returns:
        图片URL
    """
    photo_url = _resolve_attraction_photo(name)

    return {
        "success": True,
        "message": "获取图片成功",
        "data": {
            "name": name,
            "photo_url": photo_url,
        },
    }


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
        _photo_url_cache[normalized_name] = (now + _PHOTO_URL_CACHE_TTL_SECONDS, photo_url)

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
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            for _ in range(4):
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


@router.get(
    "/photo/image",
    summary="获取可导出的景点图片",
    description="按景点名称解析图片并以同源响应返回，供页面与图片/PDF 导出安全绘制。",
)
@limiter.limit("30/minute")
def get_attraction_photo_image(request: Request, name: str = Query(min_length=1, max_length=100),
                               poi_id: str = Query(default="", max_length=64, pattern=r"^[A-Za-z0-9]*$"),
                               city: str = Query(default="", max_length=32)):
    """将已解析的景点图片作为同源图片返回，不接受任意 URL，避免开放代理。"""
    if poi_id:
        # Resolve by the actual attraction ID; try other photos when a CDN URL is stale.
        try:
            service = get_amap_service()
            cache_key = "id:" + poi_id
            with _photo_url_cache_lock:
                cached = _photo_url_cache.get(cache_key)
            urls = cached[1] if cached and cached[0] > time.monotonic() else None
            if urls is None:
                with _photo_semaphore:
                    with _photo_call_lock:
                        global _photo_last_call
                        wait = _photo_last_call + _PHOTO_MIN_INTERVAL - time.monotonic()
                        if wait > 0:
                            time.sleep(wait)
                        _photo_last_call = time.monotonic()
                    detail = service.get_poi_detail(poi_id)
                urls = [p["url"] for p in (detail.get("photos") or []) if isinstance(p, dict) and p.get("url")]
                with _photo_url_cache_lock:
                    _photo_url_cache[cache_key] = (time.monotonic() + (3600 if urls else 30), urls)
            for url in urls[:3]:
                downloaded = _download_photo(url)
                if downloaded:
                    return Response(content=downloaded[0], media_type=downloaded[1],
                                    headers={"Cache-Control": "public, max-age=3600"})
        except Exception:
            logger.info("POI图片查询暂不可用")
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
