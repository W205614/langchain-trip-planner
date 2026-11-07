"""Versioned agent index commands. Java remains the source of truth."""
import json
import threading
from fastapi import HTTPException
from ..models.schemas import TripRequest, TripPlan
from ..services.rag_service import get_rag_service, CHROMA_DIR
from . import index_versions

_lock = threading.RLock()


def _purge_generations(family, where):
    # Deleted evidence must also disappear from retained and interrupted generations.
    # This is a local operation and remains available when embeddings are disabled.
    if not CHROMA_DIR.exists():return
    import chromadb
    from ..services.chroma_boundary import embedded_settings
    client=chromadb.PersistentClient(path=str(CHROMA_DIR),settings=embedded_settings(CHROMA_DIR))
    for collection in client.list_collections():
        if collection.name==family or collection.name.startswith(family+"_"):
            collection.delete(where=where)
            if collection.get(where=where,limit=1)["ids"]:
                raise RuntimeError("Deleted evidence remains in an index generation")


def history(body):
    with _lock:
        if body["operation"]=="delete":
            record_id,user_id=int(body["record_id"]),int(body["user_id"])
            key,version=f"history:{user_id}:{record_id}",int(body["job_id"])
            if not index_versions.begin(key,version):return {"success":True,"ignored":True}
            _purge_generations("trip_history",{"$and":[{"record_id":record_id},{"user_id":user_id}]})
            index_versions.complete(key,version)
            return {"success":True}
        rag = get_rag_service()
        if not rag.enabled:
            raise HTTPException(503, "RAG unavailable")
        record_id, user_id = int(body["record_id"]), int(body["user_id"])
        key, version = f"history:{user_id}:{record_id}", int(body["job_id"])
        if not index_versions.begin(key, version):
            return {"success": True, "ignored": True}
        row = body["record"]
        plan = TripPlan.model_validate_json(row["plan_json"])
        request = TripRequest(**{k: row[k] for k in ("city", "start_date", "end_date", "travel_days", "transportation", "accommodation")},
            departure_city=row.get("departure_city", ""), traveler_count=row.get("traveler_count", 1),
            room_count=row.get("room_count", 1), budget_total=row.get("budget_total"),
            preferences=json.loads(row["preferences"]), free_text_input=row.get("free_text_input", ""), constraints=plan.constraints)
        ok = rag.add_history_plan(record_id, user_id, request, plan, record_version=int(row["version"]))
        if not ok:
            raise HTTPException(503, "Index operation failed")
        index_versions.complete(key, version)
        return {"success": True}


def document(body):
    with _lock:
        if body["operation"]=="delete":
            identity,version=int(body["document_id"]),int(body["document_version"])
            key=f"document:{identity}"
            if not index_versions.begin(key,version):return {"success":True,"ignored":True}
            _purge_generations("trip_knowledge",{"document_id":identity})
            index_versions.complete(key,version)
            return {"success":True}
        rag = get_rag_service()
        if not rag.enabled:
            raise HTTPException(503, "RAG unavailable")
        identity = int(body["document_id"])
        key, version = f"document:{identity}", int(body["document_version"])
        if not index_versions.begin(key, version):
            return {"success": True, "ignored": True}
        row = body["document"]
        ok = rag.replace_public_knowledge_document(identity, row["city"], row["title"], json.loads(row["extracted_pages_json"]),
            source_tier=row["source_tier"], document_version=int(row["version"]))
        if not ok:
            raise HTTPException(503, "Index operation failed")
        index_versions.complete(key, version)
        return {"success": True}
