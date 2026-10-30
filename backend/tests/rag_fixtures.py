"""Shared isolated Chroma and Java visibility fixtures; no network calls."""
from unittest.mock import MagicMock

import pytest


_published = {}

def published_document(document_id, text="审核通过的图片事实"):
    _published[document_id] = {"status": "published", "version": 1, "text": text}


def fake_visibility(path, payload):
    if path == "/evidence/snapshot":
        return {"revision": 1, "records": [], "documents": [
            {"id": identity, "city": "北京", "title": "fixture", "source_tier": "reviewed",
             "version": record["version"], "source_text": record["text"],
             "extracted_pages_json": "[]"} for identity, record in _published.items()
            if record["status"] == "published"]}
    if path == "/evidence/revision":
        return {"revision": 1}
    assert path == "/evidence/visible"
    allowed = []
    for index, meta in enumerate(payload["candidates"]):
        if meta.get("source_type") == "multimodal":
            record = _published.get(meta.get("document_id"), {})
            if record.get("status") != "published" or record.get("version") != int(meta.get("document_version", 1)):
                continue
        if "record_id" in meta and meta.get("user_id") != payload.get("user_id"):
            continue
        allowed.append(index)
    return {"allowed": allowed}

class FakeEmbedding:
    """假的嵌入对象: 返回固定维度向量, 供维度校验测试"""

    def __init__(self, dim=3072):
        self.dim = dim
        self.model = "text-embedding-3-large"

    def embed_query(self, text):
        return [0.1] * self.dim

    def embed_documents(self, texts):
        return [[0.1] * self.dim for _ in texts]


@pytest.fixture()
def rag(tmp_path, monkeypatch):
    """隔离临时 Chroma 目录的 RagService"""
    from app.services import rag_service as rag_mod
    _published.clear()
    monkeypatch.setattr("app.agent_api.business_client.post", fake_visibility)
    monkeypatch.setattr("app.agent_api.rebuild.post", fake_visibility)
    monkeypatch.setattr("app.agent_api.rebuild.CHROMA_DIR", tmp_path / "chroma")

    monkeypatch.setattr(rag_mod, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(rag_mod, "KNOWLEDGE_DIR", tmp_path / "knowledge")
    # 复用真实 Chroma + 假嵌入, 不调网络
    svc = rag_mod.RagService.__new__(rag_mod.RagService)  # 跳过 __init__ 的真实初始化
    svc.settings = MagicMock()
    svc._collection_names = {}
    svc._degraded = False
    svc._embedding = FakeEmbedding(dim=3072)
    svc._chroma_dir = tmp_path / "chroma"  # 供测试重连用

    from langchain_chroma import Chroma
    from app.services.chroma_boundary import embedded_settings
    (tmp_path / "chroma").mkdir(parents=True, exist_ok=True)
    # store 的嵌入函数用独立实例, 避免测试里改 svc._embedding 时被 store 沿用
    svc._knowledge_store = Chroma(
        collection_name="trip_knowledge",
        embedding_function=FakeEmbedding(dim=3072),
        persist_directory=str(tmp_path / "chroma"),
        client_settings=embedded_settings(tmp_path / "chroma"),
    )
    svc._history_store = Chroma(
        collection_name="trip_history",
        embedding_function=svc._embedding,
        persist_directory=str(tmp_path / "chroma"),
        client_settings=embedded_settings(tmp_path / "chroma"),
    )
    svc._text_splitter = rag_mod.RecursiveCharacterTextSplitter(
        chunk_size=300, chunk_overlap=50,
        separators=["\n## ", "\n### ", "\n- ", "\n", "。", "；", " "],
    )
    return svc
