"""Resolve human attraction names against trusted POIs, with ambiguity rejection."""
import re
import unicodedata
from math import isfinite


def valid_attraction(p, city=""):
    """Common gate for every candidate source (not just the initial search)."""
    identity = getattr(p, "id", None) or getattr(p, "poi_id", None)
    loc = getattr(p, "location", None)
    if not identity or not getattr(p, "name", "").strip() or loc is None:
        return False
    source_city = getattr(p, "city", "")
    if city and isinstance(source_city, str) and source_city and source_city.removesuffix("市") != city.removesuffix("市"):
        return False
    if not (isfinite(loc.longitude) and isfinite(loc.latitude)
            and -180 <= loc.longitude <= 180 and -90 <= loc.latitude <= 90
            and (loc.longitude != 0 or loc.latitude != 0)):
        return False
    text = p.name + " " + (getattr(p, "type", "") or "")
    return not re.search(r"售票处|售票口|停车场|游客中心|服务中心|地铁站|公交站|酒店|宾馆|餐厅|银行|超市|足疗|洗浴", text)

# Area-level intent is allowed only for these explicit, city-scoped aliases.
# Tiananmen Square satisfies a general Tiananmen visit, not a tower admission.
ALIASES = {
    "北京": [
        {"故宫", "故宫博物院", "故宫博物馆", "紫禁城"},
        {"天安门", "天安门广场"},
        {"天坛", "天坛公园"},
        {"圆明园", "圆明园遗址公园"},
        {"八达岭", "八达岭长城"},
    ],
    "上海": [{"迪士尼", "迪士尼公园", "迪士尼乐园"}],
}


def normalized_name(value, city=""):
    value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()
    city = city.removesuffix("市").strip().casefold()
    for prefix in ([city + "市", city] if city else []):
        if value.startswith(prefix) and len(value) > len(prefix) + 1:
            value = value[len(prefix):]
            break
    return value


def resolve_name(name, candidates, city=""):
    """Return a unique best candidate, never a ticket office/entrance by substring."""
    candidates = list({getattr(p, "id", None) or p.poi_id: p for p in candidates}.values())
    raw = normalized_name(name)
    exact = [p for p in candidates if normalized_name(p.name) == raw]
    if exact:
        return exact[0] if len(exact) == 1 else None
    key = normalized_name(name, city)
    groups = ALIASES.get(city.removesuffix("市"), [])
    aliases = next((group for group in groups if key in group), {key})

    def variant(value):
        # Parenthesized branch names remain ambiguous if several branches exist.
        value = re.sub(r"\([^)]*\)$", "", value)
        value = value.replace("博物院", "博物馆")
        return re.sub(r"(?:公园|乐园|景区|风景区)$", "", value)

    ranked = []
    for p in candidates:
        candidate = normalized_name(p.name, city)
        if candidate == key:
            ranked.append((0, p))
        elif candidate in aliases:
            ranked.append((1, p))
        elif not re.search(r"售票|停车|服务中心|游客中心|商店|[出入]口|地铁|公交站|[东西南北]门|暂停|关闭", candidate):
            if len(variant(key)) >= 2 and variant(candidate) == variant(key):
                ranked.append((2, p))
    if not ranked:
        return None
    best = min(score for score, _ in ranked)
    matches = [p for score, p in ranked if score == best]
    return matches[0] if len(matches) == 1 else None
