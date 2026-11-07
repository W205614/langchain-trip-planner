"""Preview or resume a generation rebuild through the authenticated business API."""
import argparse
import json
import os
import httpx


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url",default="http://127.0.0.1:8380")
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--restart",action="store_true")
    args=parser.parse_args()
    if args.restart and not args.execute: parser.error("--restart requires --execute")
    credential=os.environ.get("TRIP_ADMIN_TOKEN")
    if not credential: parser.error("Set TRIP_ADMIN_TOKEN to an authenticated administrator Bearer token")
    with httpx.Client(timeout=310,follow_redirects=False) as client:
        response=client.post(args.base_url.rstrip("/")+"/api/rag/rebuild",
            headers={"Authorization":"Bearer "+credential},json={"preview":not args.execute,"restart":args.restart})
        if response.status_code//100!=2:
            raise SystemExit("Rebuild request failed (HTTP "+str(response.status_code)+"); inspect the private server log using its request ID")
        print(json.dumps(response.json(),ensure_ascii=False,indent=2))


if __name__=="__main__":main()
