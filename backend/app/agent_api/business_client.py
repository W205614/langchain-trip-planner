"""Authenticated read-only access to Java-owned evidence metadata."""
import os
import httpx


def post(path, payload):
    base = os.environ["BUSINESS_URL"].rstrip("/")
    with httpx.Client(timeout=5, follow_redirects=False) as client:
        response = client.post(base + "/internal/v1" + path, json=payload,
            headers={"X-Service-Key": os.environ["INTERNAL_SERVICE_KEY"]})
        response.raise_for_status()
        return response.json()


def visible(documents, user_id=None):
    documents = list(documents)
    if not documents:
        return []
    result = post("/evidence/visible", {"user_id": user_id, "candidates": [d.metadata for d in documents]})
    allowed = set(result["allowed"])
    return [doc for index, doc in enumerate(documents) if index in allowed]
