"""Bounded daily choices that preserve explicit user requirements."""
from ..models.schemas import POIInfo, TripRequest

DAILY_CANDIDATES = 4


def select_day_candidates(pois: list[POIInfo], request: TripRequest, day_index: int,
                          protected_ids: set[str] | frozenset[str] = frozenset()) -> list[POIInfo]:
    required_names = {name.strip().casefold() for name in request.constraints.must_visit}
    fixed_ids = {item.get("poi_id") for item in request.planning_input.commitments
                 if item.get("day_index") == day_index
                 and item.get("status") in {None, "confirmed"}}
    required, optional = [], []
    for poi in {poi.id: poi for poi in pois if poi.id}.values():
        target = required if (poi.id in fixed_ids or poi.id in protected_ids
                              or poi.name.strip().casefold() in required_names
                              or poi.requested_names) else optional
        target.append(poi)
    # The cap controls discretionary choices; it must not erase a user's hard
    # requirements. Request validation already bounds commitments/must-visits.
    return required + optional[:max(0, DAILY_CANDIDATES - len(required))]


def split_pois_for_days(pois: list[POIInfo], days: int) -> list[list[POIInfo]]:
    pois = list({poi.id: poi for poi in pois if poi.id}.values())
    subsets: list[list[POIInfo]] = [[] for _ in range(days)]
    if not pois:
        return subsets
    lon_span = max(p.location.longitude for p in pois) - min(p.location.longitude for p in pois)
    lat_span = max(p.location.latitude for p in pois) - min(p.location.latitude for p in pois)
    pois.sort(key=lambda p: (p.location.longitude, p.location.latitude, p.id) if lon_span >= lat_span
              else (p.location.latitude, p.location.longitude, p.id))
    for index, poi in enumerate(pois):
        subsets[min(days - 1, index * days // len(pois))].append(poi)
    return subsets
