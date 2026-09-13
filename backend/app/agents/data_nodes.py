"""景点、天气和酒店的数据节点。

复用规划器注入的高德客户端、进度回调和名称解析；不调用模型。
节点只返回自己的状态字段，由 LangGraph 合并并行结果。
"""
import logging
import time
from datetime import datetime, timedelta
from typing import List
from ..models.schemas import TripRequest, WeatherInfo
from .state import GraphState
from ..services.attraction_names import valid_attraction

logger = logging.getLogger(__name__)


class TravelDataNodes:
    def _search_attractions(self, state: GraphState) -> dict:
        """节点1: 搜索景点 (服务直调, 不走LLM)

        1. 按用户首个偏好用高德搜索景点
        2. 用 RAG 知识库补充当地必打卡景点 (按名搜索拿真实坐标),
           让 LLM 能真正采用知识库推荐的景点, 而不只是"参考"
        """
        request = state["request"]
        self._emit_progress(state, "search_attractions", 10, "正在搜索真实景点")
        logger.info("📍 步骤1: 搜索景点...")
        started_at = time.perf_counter()
        try:
            # 关键词: 优先用"城市+景点/必去景点", 避免用"美食"等偏好搜出餐馆
            keywords = request.preferences[0] if request.preferences else "景点"
            if any(k in keywords for k in ("美食", "小吃", "餐厅", "购物")):
                keywords = "必去景点"
            pois = self.amap_service.search_poi(keywords, request.city)
            # 过滤明显非景点的 POI (餐饮/酒店/购物/银行等), 避免把餐馆当景点
            _NON_ATTRACTION_TYPES = ("餐饮", "中餐厅", "餐厅", "酒店", "宾馆", "住宿", "购物", "超市", "银行", "KTV", "酒吧", "足疗", "洗浴", "火锅", "烤肉", "快餐")
            pois = [
                p for p in pois
                if valid_attraction(p, request.city) and not any(t in (p.type or "") for t in _NON_ATTRACTION_TYPES)
            ]
            logger.info(f"   找到 {len(pois)} 个景点")

            # RAG 知识库景点补充 (失败/未启用时静默跳过, 不影响主流程)
            try:
                from ..services.rag_service import get_rag_service

                known_names = {p.name for p in pois if p.name}
                for name in get_rag_service().get_knowledge_attractions(request.city):
                    if any(name in n for n in known_names):
                        continue
                    kb_pois = self.amap_service.search_poi(name, request.city)
                    match = self._resolve_required_poi(name, [p for p in kb_pois if valid_attraction(p, request.city)], request.city)
                    if match:
                        pois.append(match)
                        known_names.add(match.name or "")
                        logger.info(f"   + 知识库补充景点: {name}")
            except Exception as e:
                logger.warning(f"   ⚠️ 知识库景点补充失败(不影响主流程): {e}")

            from ..services.planning_constraints import name_key
            for name in request.constraints.must_visit:
                try:
                    candidates = self.amap_service.search_poi(name, request.city)
                    candidates = list({p.id: p for p in [*pois, *candidates] if valid_attraction(p, request.city)}.values())
                    match = self._resolve_required_poi(name, candidates, request.city)
                    if match:
                        match = match.model_copy(deep=True)
                        match.requested_names = list(dict.fromkeys([*match.requested_names, name]))
                        pois = [p for p in pois if p.id != match.id]
                        pois.append(match)
                except Exception:
                    logger.warning("必去景点查询失败，保留其它已取得的候选")
            avoided = {name_key(n) for n in request.constraints.avoid}
            unique = {p.id: p for p in pois if valid_attraction(p, request.city) and name_key(p.name) not in avoided}
            return {"attraction_pois": list(unique.values())}
        except Exception as e:
            logger.warning(f"   ⚠️ 景点搜索失败: {e}")
            return {"attraction_pois": []}
        finally:
            self._emit_trace(state, "stage_duration", stage="attraction_search", seconds=time.perf_counter() - started_at)

    def _get_weather(self, state: GraphState) -> dict:
        """节点2: 查询天气 (服务直调, 不走LLM)"""
        request = state["request"]
        self._emit_progress(state, "get_weather", 30, "正在查询天气")
        logger.info("🌤️  步骤2: 查询天气...")
        started_at = time.perf_counter()
        try:
            weather = self.amap_service.get_weather(request.city)
            relevant_weather, weather_notice = self._filter_weather_for_trip(weather, request)
            logger.info(f"   获取 {len(weather)} 天预报，其中 {len(relevant_weather)} 天与行程日期匹配")
            return {"weather_info": relevant_weather, "weather_notice": weather_notice}
        except Exception as e:
            logger.warning(f"   ⚠️ 天气查询失败: {e}")
            return {"weather_info": [], "weather_notice": "暂时无法获取天气预报，请出行前再次确认。"}
        finally:
            self._emit_trace(state, "stage_duration", stage="weather_query", seconds=time.perf_counter() - started_at)

    @staticmethod
    def _filter_weather_for_trip(weather: List[WeatherInfo], request: TripRequest) -> tuple[List[WeatherInfo], str]:
        """只保留行程日期的真实预报，避免把不相干的四天预报展示为整段行程天气。"""
        relevant = [item for item in weather if request.start_date <= item.date <= request.end_date]
        trip_dates = {
            (datetime.strptime(request.start_date, "%Y-%m-%d") + timedelta(days=index)).strftime("%Y-%m-%d")
            for index in range(request.travel_days)
        }
        covered_dates = {item.date for item in relevant}
        missing_days = len(trip_dates - covered_dates)
        if missing_days:
            return relevant, f"高德天气接口仅提供近期 4 天预报；本次行程仍有 {missing_days} 天暂无可靠预报。"
        return relevant, ""

    def _search_hotels(self, state: GraphState) -> dict:
        """节点3: 搜索酒店 (服务直调, 不走LLM)"""
        request = state["request"]
        self._emit_progress(state, "search_hotels", 45, "正在搜索住宿")
        logger.info("🏨 步骤3: 搜索酒店...")
        started_at = time.perf_counter()
        try:
            hotels = self.amap_service.search_poi(request.accommodation, request.city)
            logger.info(f"   找到 {len(hotels)} 个酒店")
            return {"hotel_pois": hotels}
        except Exception as e:
            logger.warning(f"   ⚠️ 酒店搜索失败: {e}")
            return {"hotel_pois": []}
        finally:
            self._emit_trace(state, "stage_duration", stage="hotel_search", seconds=time.perf_counter() - started_at)
