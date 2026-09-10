"""Build a new generation from authoritative data; never delete the active generation."""
import json
import os
import logging
from uuid import uuid4
from langchain_core.documents import Document
from .coordination import knowledge_lock, rag_lock


def rebuild(rag):
    from langchain_chroma import Chroma
    from ..db.database import SessionLocal
    from ..db.models import KnowledgeDocument, TripRecord
    from ..models.schemas import TripPlan
    from .history_service import trip_record_to_request
    from .rag_service import CHROMA_DIR, _KNOWLEDGE_COLLECTION, _HISTORY_COLLECTION

    if rag._embedding is None:
        return {"success": False, "message": "嵌入服务未配置", "chunks": 0}
    with knowledge_lock, rag_lock:
        generation = uuid4().hex
        names = {name: f"{name}_{generation}" for name in (_KNOWLEDGE_COLLECTION, _HISTORY_COLLECTION)}
        names["embedding_model"] = rag._embedding.model
        stores = {name: Chroma(collection_name=target, embedding_function=rag._embedding,
                              persist_directory=str(CHROMA_DIR))
                  for name, target in names.items() if name != "embedding_model"}
        documents = rag._load_knowledge_documents()
        # Dynamic POI facts have no SQL source; preserve their text if the old index is readable.
        if rag._knowledge_store is not None:
            try:
                previous = rag._knowledge_store.get(include=["documents", "metadatas"])
            except Exception:
                logging.getLogger(__name__).warning("Old dynamic index unreadable; rebuilding authoritative sources only")
                previous = {}
            for text, meta in zip(previous.get("documents", []), previous.get("metadatas", [])):
                if (meta or {}).get("source", "").startswith("gaode:"):
                    documents.append(Document(page_content=text, metadata=meta))
        history = []
        history_ids = []
        with SessionLocal() as db:
            for item in db.query(KnowledgeDocument).filter(KnowledgeDocument.status == "published").all():
                if not item.source_text.strip():
                    raise RuntimeError("Published source text is missing; rebuild aborted")
                pages = item.source_text.split("\n\n## ")
                for page, text in enumerate(pages, 1):
                    for index, chunk in enumerate(rag._text_splitter.split_text(text)):
                        documents.append(Document(page_content=chunk, metadata={
                            "city": item.city, "source": item.title, "source_type": "multimodal",
                            "source_tier": item.source_tier, "document_id": item.id,
                            "document_version": item.version, "page": page,
                            "chunk_id": f"submission:{item.id}:{page}:{index}",
                        }))
            for record in db.query(TripRecord).all():
                history.append(Document(page_content=rag._plan_to_text(trip_record_to_request(record),
                    TripPlan.model_validate_json(record.plan_json)), metadata={
                        "record_id": record.id, "user_id": record.user_id, "city": record.city}))
                history_ids.append(f"history-{record.user_id}-{record.id}")
        if documents:
            stores[_KNOWLEDGE_COLLECTION].add_documents(documents)
        if history:
            stores[_HISTORY_COLLECTION].add_documents(history, ids=history_ids)
        for name, expected in ((_KNOWLEDGE_COLLECTION, len(documents)), (_HISTORY_COLLECTION, len(history))):
            if stores[name]._collection.count() != expected:
                raise RuntimeError("New index count verification failed")
            if expected:
                stores[name].similarity_search("验证索引", k=1)
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        temporary = CHROMA_DIR / f"active-{generation}.tmp"
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(names, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, CHROMA_DIR / "active-index.json")
        rag._collection_names = names
        rag._knowledge_store = stores[_KNOWLEDGE_COLLECTION]
        rag._history_store = stores[_HISTORY_COLLECTION]
        rag._degraded = False
        return {"success": True, "message": "新索引已验证并切换，旧集合保留", "chunks": len(documents),
                "history_records": len(history), "generation": generation}
