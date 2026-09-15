"""Versioned agent index commands. Java remains the source of truth."""
import json
import threading
from fastapi import HTTPException
from ..models.schemas import TripRequest, TripPlan
from ..services.rag_service import get_rag_service
from . import index_versions

_lock = threading.RLock()


def history(body):
    with _lock:
        rag = get_rag_service()
        if not rag.enabled:
            raise HTTPException(503, "RAG unavailable")
        record_id, user_id = int(body["record_id"]), int(body["user_id"])
        key, version = f"history:{user_id}:{record_id}", int(body["job_id"])
        if not index_versions.begin(key, version):
            return {"success": True, "ignored": True}
        if body["operation"] == "delete":
            ok = rag.delete_history_plan(record_id, user_id)
        else:
            row = body["record"]
            plan = TripPlan.model_validate_json(row["plan_json"])
            request = TripRequest(**{k: row[k] for k in ("city", "start_date", "end_date", "travel_days", "transportation", "accommodation")},
                preferences=json.loads(row["preferences"]), free_text_input=row.get("free_text_input", ""), constraints=plan.constraints)
            ok = rag.add_history_plan(record_id, user_id, request, plan, record_version=int(row["version"]))
        if not ok:
            raise HTTPException(503, "Index operation failed")
        index_versions.complete(key, version)
        return {"success": True}


def document(body):
    with _lock:
        rag = get_rag_service()
        if not rag.enabled:
            raise HTTPException(503, "RAG unavailable")
        identity = int(body["document_id"])
        key, version = f"document:{identity}", int(body["document_version"])
        if not index_versions.begin(key, version):
            return {"success": True, "ignored": True}
        if body["operation"] == "delete":
            ok = rag.delete_public_knowledge_document(identity)
        else:
            row = body["document"]
            ok = rag.replace_public_knowledge_document(identity, row["city"], row["title"], json.loads(row["extracted_pages_json"]),
                source_tier=row["source_tier"], document_version=int(row["version"]))
        if not ok:
            raise HTTPException(503, "Index operation failed")
        index_versions.complete(key, version)
        return {"success": True}
