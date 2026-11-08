"""Bounded independent choices, with geometry used only for candidate ranking."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from ..models.schemas import POIInfo, TripRequest, Location
from ..services.assistant_intent import daily_attraction_target
from ..services.travel_facts import suspected_same_visit

DAILY_CANDIDATES = 4
GENERATION_POLICY_VERSION = "day-candidates-v2-independent-window"


def day_candidate_limit(request: TripRequest) -> int:
    return min(20, max(DAILY_CANDIDATES, (daily_attraction_target(request.free_text_input) or 2) + 2))


def independent_candidate_groups(pois: list[POIInfo], request: TripRequest) -> list[list[POIInfo]]:
    from ..services.time_planner import fact_current
    groups: list[list[POIInfo]] = []
    facts = [fact for fact in request.planning_input.poi_relations if fact_current(fact, "verification")]

    def relation(left, right):
        ids = {left.id, right.id}
        declared = [fact for fact in request.planning_input.poi_relations
                    if {fact.get("parent_poi_id"), fact.get("child_poi_id")} == ids]
        current = [fact for fact in facts if fact in declared]
        if len(current) == 1:
            return current[0]["relation"]
        return "included" if declared or suspected_same_visit(left, right) else None

    for poi in {poi.id: poi for poi in pois if poi.id}.values():
        merged = [poi]
        for group in groups[:]:
            if (any(relation(left, right) == "included" for left in merged for right in group)
                    and not any(relation(left, right) == "separate" for left in merged for right in group)):
                merged.extend(group)
                groups.remove(group)
        groups.append(merged)
    return groups


def independent_candidate_count(pois: list[POIInfo], request: TripRequest) -> int:
    return len(independent_candidate_groups(pois, request))


def _required(poi, request, fixed_ids, protected_ids):
    return (poi.id in fixed_ids or poi.id in protected_ids or poi.requested_names
            or poi.name.strip().casefold() in {name.strip().casefold() for name in request.constraints.must_visit})


def select_day_candidates(pois: list[POIInfo], request: TripRequest, day_index: int,
                          protected_ids: set[str] | frozenset[str] = frozenset()) -> list[POIInfo]:
    fixed_ids = {item.get("poi_id") for item in request.planning_input.commitments
                 if item.get("day_index") == day_index
                 and item.get("status") in {None, "confirmed"}}
    unique = list({poi.id: poi for poi in pois if poi.id}.values())
    required = [poi for poi in unique if _required(poi, request, fixed_ids, protected_ids)]
    required_ids = {poi.id for poi in required}
    optional = [min(group, key=lambda poi: (len(poi.name), unique.index(poi)))
                for group in independent_candidate_groups(unique, request)
                if not any(poi.id in required_ids for poi in group)]
    optional.sort(key=lambda poi: unique.index(poi))
    # The cap controls discretionary choices; it must not erase a user's hard
    # requirements. Request validation already bounds commitments/must-visits.
    return required + optional[:max(0, day_candidate_limit(request) - len(required))]


def split_pois_for_days(pois: list[POIInfo], days: int, *, request: TripRequest | None = None,
                        hotel_pois: list[POIInfo] | None = None) -> list[list[POIInfo]]:
    pois = list({poi.id: poi for poi in pois if poi.id}.values())
    subsets: list[list[POIInfo]] = [[] for _ in range(days)]
    if not pois:
        return subsets
    if request is not None:
        return _allocate_for_windows(pois, days, request, hotel_pois or [])
    lon_span = max(p.location.longitude for p in pois) - min(p.location.longitude for p in pois)
    lat_span = max(p.location.latitude for p in pois) - min(p.location.latitude for p in pois)
    pois.sort(key=lambda p: (p.location.longitude, p.location.latitude, p.id) if lon_span >= lat_span
              else (p.location.latitude, p.location.longitude, p.id))
    for index, poi in enumerate(pois):
        subsets[min(days - 1, index * days // len(pois))].append(poi)
    return subsets


def day_candidate_anchors(request, index, hotels=()):
    from ..services.time_planner import city_for_day, fact_current
    city = city_for_day(request, index)
    locations = []
    for item in request.planning_input.commitments:
        if item.get("day_index") != index or item.get("status") not in {None, "confirmed"}:
            continue
        for city_key, location_key in (("origin_city", "origin_location"), ("destination_city", "destination_location")):
            if item.get(city_key) == city and item.get(location_key):
                locations.append(Location.model_validate(item[location_key]))
    for stay in request.planning_input.hotel_stays:
        if (stay.get("day_index") in {index - 1, index} and (stay.get("city") or city) == city
                and stay.get("location") and (not stay.get("source") or fact_current(stay, "verification"))):
            locations.append(Location.model_validate(stay["location"]))
    # Searched accommodation is a soft ranking reference, never a verified endpoint.
    if not locations and hotels:
        locations.append(hotels[0].location)
    return [SimpleNamespace(location=location) for location in locations]


def restaurants_for_day(restaurants, attractions, request, day_index, hotels=()):
    from ..services.plan_quality import haversine_km
    points = [*attractions, *day_candidate_anchors(request, day_index, hotels)]
    if not points:
        return restaurants[:8]
    return sorted(restaurants, key=lambda poi: sum(haversine_km(poi, point) for point in points) / len(points))[:8]


def _allocate_for_windows(pois, days, request, hotels):
    from ..services.plan_quality import haversine_km
    from ..services.time_planner import day_time_bounds
    subsets = [[] for _ in range(days)]
    dates = [(datetime.fromisoformat(request.start_date) + timedelta(days=index)).date().isoformat() for index in range(days)]
    bounds = [day_time_bounds(request, index, date) for index, date in enumerate(dates)]
    short_first = sorted(range(days), key=lambda index: (bounds[index][1] - bounds[index][0], index))
    fixed = {item.get("poi_id"): item["day_index"] for item in request.planning_input.commitments
             if item.get("status") in {None, "confirmed"} and isinstance(item.get("day_index"), int)
             and 0 <= item["day_index"] < days}
    required = [poi for poi in pois if _required(poi, request, fixed, frozenset())]
    required_ids = {poi.id for poi in required}
    optional = [min(group, key=lambda poi: (len(poi.name), pois.index(poi)))
                for group in independent_candidate_groups(pois, request)
                if not any(poi.id in required_ids for poi in group)]
    for poi in required:
        index = fixed.get(poi.id)
        if index is None:
            index = min(range(days), key=lambda i: (len(subsets[i]), -(bounds[i][1] - bounds[i][0]), i))
        subsets[index].append(poi)

    references = [day_candidate_anchors(request, index, hotels) for index in range(days)]
    if optional:
        center = min(optional, key=lambda left: sum(haversine_km(left, right) for right in optional))
        references = [items or [center] for items in references]

    def take(index):
        near = [*references[index], *subsets[index]]
        poi = min(optional, key=lambda candidate: (sum(haversine_km(candidate, point) for point in near) / len(near), pois.index(candidate)))
        optional.remove(poi)
        subsets[index].append(poi)

    target = daily_attraction_target(request.free_text_input) or 2
    # Give every day independent choices before spending the remaining pool on spares.
    for _ in range(target):
        for index in short_first:
            if optional and independent_candidate_count(subsets[index], request) < target:
                take(index)
    for _ in range(day_candidate_limit(request)):
        for index in reversed(short_first):
            if optional and len(subsets[index]) < day_candidate_limit(request):
                take(index)
    return subsets
