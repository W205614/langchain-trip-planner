"""POI相关API路由"""

import logging
import threading
import time
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ...services.amap_service import get_amap_service

router = APIRouter(prefix="/poi", tags=["POI"])

logger = logging.getLogger(__name__)

# 高德个人开发者 key 有 QPS 限制(约3-5), 前端结果页会并发请求所有景点图片。
# 用信号量限制同时并发 + 最小间隔节流, 双保险防止 CUQPS_HAS_EXCEEDED_THE_LIMIT 超限。
_photo_semaphore = threading.BoundedSemaphore(3)
_PHOTO_MIN_INTERVAL = 0.4  # 高德图片调用最小间隔(秒), 0.4s/次 ≈ 2.5 QPS
_photo_last_call = 0.0
_photo_call_lock = threading.Lock()


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
def get_poi_detail(poi_id: str):
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
def search_poi(keywords: str, city: str = "北京"):
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
def get_attraction_photo(name: str):
    """
    获取景点图片

    优先取高德POI实景图(国内图源, 快且稳); 高德无图时退回必应图片搜索 (CDN直链)。
    两者都无则返回 null, 前端用占位图兜底。

    Args:
        name: 景点名称

    Returns:
        图片URL
    """
    # 高德POI图片 (国内图源); 信号量限并发 + 间隔节流限QPS, 防止CUQPS超限
    amap_service = get_amap_service()
    with _photo_semaphore:
        photo_url = _rate_limited_photo_call(amap_service, name)

    # 高德无图 → 必应图片兜底 (免费稳定, 返回 CDN 直链)
    if not photo_url:
        photo_url = _bing_image_fallback(name)

    return {
        "success": True,
        "message": "获取图片成功",
        "data": {
            "name": name,
            "photo_url": photo_url,
        },
    }


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
