"""并行数据节点与行程生成节点之间的状态契约。"""
from typing import Callable, TypedDict, List
from ..models.schemas import TripRequest, TripPlan, POIInfo, WeatherInfo

class GraphState(TypedDict, total=False):
    """LangGraph 工作流状态"""
    request: TripRequest               # 用户旅行请求
    attraction_pois: List[POIInfo]     # 景点搜索结果
    weather_info: List[WeatherInfo]    # 天气信息
    weather_notice: str                # 天气预报覆盖范围说明
    hotel_pois: List[POIInfo]          # 酒店搜索结果
    rag_context: str                   # 与地图数据并行准备的公共/用户可见资料上下文
    trip_plan: TripPlan                # 最终行程计划
    error: bool                        # 是否出错(用于条件路由)
    user_id: int                       # RAG 历史检索的用户隔离键
    progress_callback: Callable[[str, int, str], None]
    trace_callback: Callable[[str, dict], None]  # 仅用于评测/观测，不参与业务决策
