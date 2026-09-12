"""Small opt-in live check against official Amap MCP, without LLM or database writes."""
import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["AMAP_TRANSPORT"] = "mcp"
    os.environ["AMAP_MCP_SEARCH_LIMIT"] = "2"
    logging.disable(logging.CRITICAL)  # SDK/httpx logs must not print the credential-bearing URL.
    from app.services.amap_service import get_amap_service, close_amap_service

    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "transport": "mcp", "llm_called": False}
    try:
        service = get_amap_service()
        report["tools"] = sorted(tool["name"] for tool in service.mcp_client.tools())
        origin = service.search_poi("故宫博物院", "北京")[0]
        destination = service.search_poi("天坛公园", "北京")[0]
        report["pois"] = [{"id": p.id, "name": p.name, "location": p.location.model_dump(),
                           "has_opening_hours": bool(p.opening_hours), "has_photo": bool(p.photos)}
                          for p in (origin, destination)]
        report["weather_days"] = len(service.get_weather("北京"))
        report["geocode_available"] = bool(service.geocode("北京市"))
        report["detail_id_matches"] = service.get_poi_detail(origin.id).get("id") == origin.id
        report["routes"] = {mode: service.plan_route_by_locations(origin.location, destination.location, mode, "北京")
                            for mode in ("walking", "driving", "transit")}
        report["passed"] = report["detail_id_matches"] and report["weather_days"] > 0 and report["geocode_available"] and all(report["routes"].values())
    except Exception as exc:
        report.update(passed=False, error_type=type(exc).__name__)
    finally:
        close_amap_service()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
