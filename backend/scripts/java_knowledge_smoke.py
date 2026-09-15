"""Java review lifecycle with explicit offline parser/index fixtures, no paid calls."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4
import httpx


def run(output):
    compose=["docker","compose","-p","trip-validation","-f","docker-compose.validation.yml"]
    with httpx.Client(base_url="http://127.0.0.1:18080",timeout=30) as client:
        assert client.get("/api/validation/fixture").json()["offline_fixture"]
        registered=client.post("/api/auth/register",json={"username":"review_"+uuid4().hex[:10],"password":"test-review123"})
        registered.raise_for_status();client.headers["Authorization"]="Bearer "+registered.json()["access_token"]
        uid=client.get("/api/auth/me").json()["id"]
        assert client.get("/api/knowledge/admin/submissions").status_code==403
        subprocess.run(compose+["exec","-T","postgres","psql","-U","trip","-d","trip","-v","ON_ERROR_STOP=1","-c",f"UPDATE users SET is_admin=true WHERE id={int(uid)}"],check=True,capture_output=True)
        png=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jV1kAAAAASUVORK5CYII=")
        upload=client.post("/api/knowledge/submissions",data={"city":"北京","title":"离线复核验收"},files={"file":("review.png",png,"image/png")})
        assert upload.status_code==201,upload.text
        document=upload.json()["data"];identity=document["id"]
        assert document["status"]=="pending"
        prefix=f"/api/knowledge/admin/submissions/{identity}"
        assert client.get(prefix+"/original").content==png
        assert client.post(prefix+"/approve",json={"source_tier":"reviewed","note":"仅用于离线验收"}).status_code==200
        for _ in range(100):
            document=client.get(prefix+"/preview").json()["data"]
            if document["status"]=="awaiting_review":break
            time.sleep(.2)
        else:raise AssertionError("Parser never reached human review")
        version=document["version"]
        assert client.post(prefix+"/publish",json={"version":version-1}).status_code==409
        edited=client.put(prefix+"/extraction",json={"version":version,"pages":["## 人工核对后的内容\n- 不添加图片之外的事实"]})
        assert edited.status_code==200,edited.text
        assert client.post(prefix+"/publish",json={"version":version}).status_code==409
        current=edited.json()["data"]["version"]
        assert client.post(prefix+"/publish",json={"version":current}).status_code==200
        for _ in range(100):
            document=client.get(prefix+"/preview").json()["data"]
            if document["status"]=="published":break
            time.sleep(.2)
        else:raise AssertionError("Publish never acknowledged")
        assert client.delete(prefix).status_code==200
        assert client.get(prefix+"/preview").status_code==404
        report={"mode":"java_http_offline_parser_index_fixtures","admin_required":True,"original_file_equal":True,
            "parser_waited_for_human_review":True,"stale_publish_rejected":True,"published_current_version":True,"deleted_preview_hidden":True,"paid_calls":0}
        output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2),encoding="utf-8");print(json.dumps(report))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,required=True)
    run(parser.parse_args().output)
