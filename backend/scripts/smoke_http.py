"""Nginx boundary smoke using only synthetic uploads and an isolated fixture account."""
import argparse
import uuid
import httpx


def run(url):
    with httpx.Client(base_url=url, timeout=60) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/metrics").status_code == 404
        assert client.post("/api/rag/rebuild").status_code == 401
        response = client.post("/api/auth/register", json={"username": "smoke_"+uuid.uuid4().hex[:8], "password": "testing123"})
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        assert client.post("/api/rag/rebuild").status_code == 403
        for size, expected in ((100, 201), (2*1024*1024, 201), (20*1024*1024, 201), (20*1024*1024+1, 413)):
            payload = b"\xff\xd8\xff" + b"x" * (size-3)
            response = client.post("/api/knowledge/submissions", data={"city": "北京", "title": "合成边界测试文件"},
                                   files={"file": ("synthetic.jpg", payload, "image/jpeg")})
            assert response.status_code == expected, (size, response.status_code)
        print("Nginx smoke: health, admin authorization, private metrics and four upload boundaries passed")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:18080")
    run(p.parse_args().base_url)
