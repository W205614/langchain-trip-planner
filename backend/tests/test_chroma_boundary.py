"""Executable assumptions behind the narrowly scoped embedded-Chroma VEX."""
import pytest


def test_embedded_client_overrides_remote_api_and_authorization_environment(monkeypatch, tmp_path):
    pytest.importorskip("chromadb")
    from app.services.chroma_boundary import embedded_settings
    monkeypatch.setenv("CHROMA_API_IMPL", "chromadb.api.fastapi.FastAPI")
    monkeypatch.setenv("CHROMA_SERVER_HOST", "untrusted.invalid")
    monkeypatch.setenv("CHROMA_SERVER_AUTHZ_PROVIDER", "untrusted.AuthorizationProvider")
    settings = embedded_settings(tmp_path)
    assert settings.chroma_api_impl == "chromadb.api.rust.RustBindingsAPI"
    assert settings.is_persistent and not settings.anonymized_telemetry
    assert settings.chroma_server_host is None
    assert settings.chroma_server_authz_provider is None


def test_embedded_roundtrip_uses_supplied_embeddings_without_network(tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    from langchain_chroma import Chroma
    from app.services.chroma_boundary import embedded_settings
    import httpx
    import requests
    def network_forbidden(*args, **kwargs):
        raise AssertionError("Embedded vector storage must not call a Chroma HTTP server or remote embedding loader")
    monkeypatch.setattr(httpx.Client, "request", network_forbidden)
    monkeypatch.setattr(requests.Session, "request", network_forbidden)
    class Embeddings:
        def embed_documents(self, values): return [[1.0, 0.0, 0.0] for _ in values]
        def embed_query(self, value): return [1.0, 0.0, 0.0]
    store = Chroma(collection_name="embedded_security_boundary", embedding_function=Embeddings(),
                   persist_directory=str(tmp_path), client_settings=embedded_settings(tmp_path))
    store.add_texts(["北京的离线核验资料"], metadatas=[{"user_id": 1}])
    assert store.similarity_search("离线", k=1)[0].metadata["user_id"] == 1


def test_agent_does_not_expose_chroma_server_collection_routes(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.agent_api import main
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", "internal-test-key-01234567890123456789")
    monkeypatch.setenv("BUSINESS_URL", "http://business-test:9000")
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    with TestClient(main.app) as client:
        payload = {"name": "attack", "configuration": {"embedding_function": {"type": "huggingface", "trust_remote_code": True}}}
        response = client.post("/api/v2/tenants/default_tenant/databases/default_database/collections", json=payload)
        assert response.status_code == 404
