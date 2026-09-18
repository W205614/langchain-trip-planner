"""One completion contract for generated, edited and revised itineraries."""
from .attraction_names import valid_attraction

POLICY = "reliability-v1"


def classify(plan, request, report):
    issues = []
    def add(code, scope, reason, action, blocking=True, retryable=False):
        issues.append(dict(code=code, scope=scope, reason=reason, action=action,
                           blocking=blocking, retryable=retryable))
    for warning in report.get("warnings", []):
        add("CONSTRAINT_UNSATISFIED", "plan", warning, "修改要求或调整行程后重新检查")
    for day in plan.days:
        for attraction in day.attractions:
            if not valid_attraction(attraction):
                add("INVALID_POI", f"day:{day.day_index}", "景点身份或坐标无效", "重新选择可信景点")
    route_missing_days = []
    for day in report.get("day_checks", []):
        if day.get("route_minutes") is None:
            route_missing_days.append(day["day_index"] + 1)
        if request.constraints.max_inter_stop_walking_km is not None and day.get("inter_stop_walking_km") is None:
            add("WALKING_LIMIT_UNVERIFIED", f"day:{day['day_index']}", "无法核验指定步行上限",
                "稍后重试或调整交通要求", retryable=True)
    if route_missing_days:
        days = "、".join(f"第{index}天" for index in route_missing_days)
        add("ROUTE_UNAVAILABLE", "plan", f"{days}部分景点间路线暂不可用，Agent 已保留可继续调整的行程",
            "可直接查看行程；出发前确认交通，或在具体行程中调整景点", False, True)
    for index in report.get("degraded_days", []):
        add("RULE_FALLBACK", f"day:{index}", "模型未完成，本日使用可信景点规则安排", "核对安排或重新规划", False, True)
    for gap in report.get("data_gaps", []):
        add("DATA_UNVERIFIED", "plan", gap, "出行前向官方来源确认", False)
    if plan.weather_notice:
        add("WEATHER_UNAVAILABLE", "plan", plan.weather_notice, "出行前查询天气", False, True)
    for notice in plan.enrichment_notices:
        add("RAG_UNAVAILABLE", "plan", notice, "可继续查看行程，攻略资料请稍后查询", False, True)
    blocking = any(i["blocking"] for i in issues)
    # Fixed product boundaries (reservation / in-attraction walking) remain visible,
    # but are not counted as a runtime outage on every otherwise complete request.
    degraded = any(i["code"] in {"RULE_FALLBACK", "RAG_UNAVAILABLE", "WEATHER_UNAVAILABLE", "ROUTE_UNAVAILABLE"} for i in issues)
    report.update(completion_policy=POLICY, issues=issues,
                  outcome="draft" if blocking else "degraded" if degraded else "complete")
    return report


def error_info(code):
    messages = {
        "TRUSTED_POI_UNAVAILABLE": ("暂未获取到可信景点，请稍后重试或更换目的地", True),
        "TASK_TIMEOUT": ("规划超过时间预算，请稍后重试或缩短行程", True),
        "PROCESS_INTERRUPTED": ("服务重启中断了规划，可重新提交", True),
        "VERSION_CONFLICT": ("原行程已被修改，请重新打开后发起改排", False),
        "RESULT_DELETED": ("原行程已删除，请重新规划", False),
        "UPSTREAM_CONFIG_ERROR": ("服务配置异常，请联系管理员；重复提交暂时无法恢复", False),
        "GENERATION_FAILED": ("规划未完成，请稍后重试；若持续失败，请联系管理员", True),
    }
    message, retryable = messages.get(code, messages["GENERATION_FAILED"])
    return dict(message=message, retryable=retryable)
