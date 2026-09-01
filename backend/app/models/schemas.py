"""数据模型定义"""

from typing import Any, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from datetime import date


# ============ 请求模型 ============

class TripRequest(BaseModel):
    """旅行规划请求"""
    city: str = Field(..., max_length=32, description="目的地城市", json_schema_extra={"example": "北京"})
    start_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="开始日期 YYYY-MM-DD", json_schema_extra={"example": "2025-06-01"})
    end_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="结束日期 YYYY-MM-DD", json_schema_extra={"example": "2025-06-03"})
    travel_days: int = Field(..., description="旅行天数", ge=1, le=30, json_schema_extra={"example": 3})
    transportation: str = Field(..., max_length=32, description="交通方式", json_schema_extra={"example": "公共交通"})
    accommodation: str = Field(..., description="住宿偏好", json_schema_extra={"example": "经济型酒店"})
    preferences: List[str] = Field(default=[], description="旅行偏好标签", json_schema_extra={"example": ["历史文化", "美食"]})
    free_text_input: Optional[str] = Field(
        default="", max_length=500,
        description="额外要求", json_schema_extra={"example": "希望多安排一些博物馆"},
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "city": "北京",
                "start_date": "2025-06-01",
                "end_date": "2025-06-03",
                "travel_days": 3,
                "transportation": "公共交通",
                "accommodation": "经济型酒店",
                "preferences": ["历史文化", "美食"],
                "free_text_input": "希望多安排一些博物馆"
            }
        }
    )

    @model_validator(mode="after")
    def _check_dates(self):
        """校验真实日期及天数，避免字符串比较或前端绕过造成脏计划。"""
        try:
            start = date.fromisoformat(self.start_date)
            end = date.fromisoformat(self.end_date)
        except ValueError as exc:
            raise ValueError("日期必须是有效的 YYYY-MM-DD") from exc
        if end < start:
            raise ValueError("结束日期不能早于开始日期")
        if (end - start).days + 1 != self.travel_days:
            raise ValueError("travel_days 必须与开始和结束日期（含首尾）一致")
        return self


class TripRevisionRequest(BaseModel):
    """对已保存行程中某一天发起受限的增量改排请求。"""

    day_index: int = Field(..., ge=0, le=29, description="要改排的天序号（从 0 开始）")
    instruction: str = Field(
        ...,
        min_length=2,
        max_length=500,
        description="本次改排要求，例如：下雨，改为室内博物馆并减少步行",
    )


class TravelPreferenceRequest(BaseModel):
    """用户显式保存的可复用旅行偏好。自由文本不进入长期记忆。"""

    preferences: List[str] = Field(default_factory=list, max_length=12)
    transportation: str = Field(default="公共交通", min_length=1, max_length=32)
    accommodation: str = Field(default="经济型酒店", min_length=1, max_length=32)


class POISearchRequest(BaseModel):
    """POI搜索请求"""
    keywords: str = Field(..., description="搜索关键词", json_schema_extra={"example": "故宫"})
    city: str = Field(..., description="城市", json_schema_extra={"example": "北京"})
    citylimit: bool = Field(default=True, description="是否限制在城市范围内")


class RouteRequest(BaseModel):
    """路线规划请求"""
    origin_address: str = Field(..., description="起点地址", json_schema_extra={"example": "北京市朝阳区阜通东大街6号"})
    destination_address: str = Field(..., description="终点地址", json_schema_extra={"example": "北京市海淀区上地十街10号"})
    origin_city: Optional[str] = Field(default=None, description="起点城市")
    destination_city: Optional[str] = Field(default=None, description="终点城市")
    route_type: str = Field(default="walking", description="路线类型: walking/driving/transit")


# ============ 响应模型 ============

class Location(BaseModel):
    """地理位置"""
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")


class Attraction(BaseModel):
    """景点信息"""
    name: str = Field(..., description="景点名称")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    visit_duration: int = Field(..., description="建议游览时间(分钟)")
    description: str = Field(..., description="景点描述")
    category: Optional[str] = Field(default="景点", description="景点类别")
    rating: Optional[float] = Field(default=None, description="评分")
    photos: Optional[List[str]] = Field(default_factory=list, description="景点图片URL列表")
    # 成功行程中的景点必须能追溯到高德候选 POI；不接受模型自行编造的名称或坐标。
    poi_id: str = Field(..., min_length=1, description="高德 POI ID")
    image_url: Optional[str] = Field(default=None, description="图片URL")
    ticket_price: int = Field(default=0, description="门票价格(元)")


class AttractionDraft(BaseModel):
    """LLM 的轻量景点草稿；真实 POI 事实字段由后端候选集回填。"""
    poi_id: str = Field(..., min_length=1, description="高德候选 POI ID")
    visit_duration: int = Field(default=120, ge=30, le=480, description="建议游览时间(分钟)")
    description: str = Field(default="", max_length=240, description="游览建议")
    category: Optional[str] = Field(default="景点", description="景点类别")
    ticket_price: int = Field(default=0, ge=0, description="门票估算(元)")


class Meal(BaseModel):
    """餐饮信息"""
    type: str = Field(..., description="餐饮类型: breakfast/lunch/dinner/snack")
    name: str = Field(..., description="餐饮名称")
    address: Optional[str] = Field(default=None, description="地址")
    location: Optional[Location] = Field(default=None, description="经纬度坐标")
    description: Optional[str] = Field(default=None, description="描述")
    estimated_cost: int = Field(default=0, description="预估费用(元)")


class DayPlanDraft(BaseModel):
    """单日 LLM 轻量输出，解析后再构造完整 DayPlan。"""
    description: str = Field(default="", max_length=300, description="当日概述")
    attractions: List[AttractionDraft] = Field(default_factory=list, description="景点草稿")
    meals: List[Meal] = Field(default_factory=list, description="餐食安排")


class Hotel(BaseModel):
    """酒店信息"""
    name: str = Field(..., description="酒店名称")
    address: str = Field(default="", description="酒店地址")
    location: Optional[Location] = Field(default=None, description="酒店位置")
    price_range: str = Field(default="", description="价格范围")
    rating: str = Field(default="", description="评分")
    distance: str = Field(default="", description="距离景点距离")
    type: str = Field(default="", description="酒店类型")
    estimated_cost: int = Field(default=0, description="预估费用(元/晚)")


class DayPlan(BaseModel):
    """单日行程"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_index: int = Field(..., description="第几天(从0开始)")
    description: str = Field(..., description="当日行程描述")
    transportation: str = Field(..., description="交通方式")
    accommodation: str = Field(..., description="住宿")
    hotel: Optional[Hotel] = Field(default=None, description="推荐酒店")
    attractions: List[Attraction] = Field(default=[], description="景点列表")
    meals: List[Meal] = Field(default=[], description="餐饮列表")
    # 降级信息为兼容性新增字段：真实 POI 兜底时让前端和质量报告明确可见。
    generation_mode: str = Field(default="llm", description="生成来源: llm 或 fallback")
    fallback_reason: Optional[str] = Field(default=None, description="降级原因（如 timeout）")


class WeatherInfo(BaseModel):
    """天气信息"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_weather: str = Field(default="", description="白天天气")
    night_weather: str = Field(default="", description="夜间天气")
    day_temp: Union[int, str] = Field(default=0, description="白天温度")
    night_temp: Union[int, str] = Field(default=0, description="夜间温度")
    wind_direction: str = Field(default="", description="风向")
    wind_power: str = Field(default="", description="风力")

    @field_validator('day_temp', 'night_temp', mode='before')
    @classmethod
    def parse_temperature(cls, v):
        """解析温度,移除°C等单位; None(无天气数据)转0"""
        if v is None:
            return 0
        if isinstance(v, str):
            # 移除°C, ℃等单位符号
            v = v.replace('°C', '').replace('℃', '').replace('°', '').strip()
            try:
                return int(v)
            except ValueError:
                return 0
        return v


class Budget(BaseModel):
    """预算信息"""
    total_attractions: int = Field(default=0, description="景点门票总费用")
    total_hotels: int = Field(default=0, description="酒店总费用")
    total_meals: int = Field(default=0, description="餐饮总费用")
    total_transportation: int = Field(default=0, description="交通总费用")
    total: int = Field(default=0, description="总费用")


class TripPlan(BaseModel):
    """旅行计划"""
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    days: List[DayPlan] = Field(..., description="每日行程")
    weather_info: List[WeatherInfo] = Field(default=[], description="天气信息")
    weather_notice: str = Field(default="", description="天气预报覆盖范围说明")
    overall_suggestions: str = Field(..., description="总体建议")
    budget: Optional[Budget] = Field(default=None, description="预算信息")


class TripPlanResponse(BaseModel):
    """旅行计划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[TripPlan] = Field(default=None, description="旅行计划数据")
    quality: Optional[dict[str, Any]] = Field(default=None, description="确定性质量校验结果")
    cached: bool = Field(default=False, description="是否由幂等缓存返回")


class POIInfo(BaseModel):
    """POI信息"""
    id: str = Field(..., description="POI ID")
    name: str = Field(..., description="名称")
    type: str = Field(..., description="类型")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    tel: Optional[str] = Field(default=None, description="电话")


class POISearchResponse(BaseModel):
    """POI搜索响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[POIInfo] = Field(default=[], description="POI列表")


class RouteInfo(BaseModel):
    """路线信息"""
    distance: float = Field(..., description="距离(米)")
    duration: int = Field(..., description="时间(秒)")
    route_type: str = Field(..., description="路线类型")
    description: str = Field(..., description="路线描述")


class RouteResponse(BaseModel):
    """路线规划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[RouteInfo] = Field(default=None, description="路线信息")


class WeatherResponse(BaseModel):
    """天气查询响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[WeatherInfo] = Field(default=[], description="天气信息")


# ============ 错误响应 ============

class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = Field(default=False, description="是否成功")
    message: str = Field(..., description="错误消息")
    error_code: Optional[str] = Field(default=None, description="错误代码")

