"""Reconstruct derived indexes in an isolated directory using deterministic embeddings."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if not os.environ.get("DATA_DIR", "").startswith("/tmp/trip-restore-"):
    raise SystemExit("Recovery verification requires a /tmp/trip-restore-* directory")

from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.services.rag_service import RagService
from app.db.database import SessionLocal
from app.db.models import TripRecord, KnowledgeDocument


class DeterministicEmbedding(Embeddings):
    model = "recovery-fixture-16"
    def embed_documents(self, texts):
        return [[0.1]*16 for text in texts]
    def embed_query(self, text):
        return [0.1]*16


rag = RagService.__new__(RagService)
rag._embedding = DeterministicEmbedding()
rag._knowledge_store = rag._history_store = None
rag._text_splitter = RecursiveCharacterTextSplitter(chunk_size=300, chunk_overlap=50)
result = rag.build_knowledge_index()
assert result["success"], result
with SessionLocal() as db:
    records = db.query(TripRecord).all()
    assert result["history_records"] == len(records) and records
    for record in records:
        item = rag._history_store.get(ids=[f"history-{record.user_id}-{record.id}"])
        assert item["metadatas"][0]["user_id"] == record.user_id
    published = db.query(KnowledgeDocument).filter(KnowledgeDocument.status == "published").all()
    assert published
    for item in published:
        indexed = rag._knowledge_store.get(where={"document_id": item.id})
        assert indexed["documents"]
    print({"restored_history": len(records), "restored_published": len(published), "mode": "deterministic_embedding"})
