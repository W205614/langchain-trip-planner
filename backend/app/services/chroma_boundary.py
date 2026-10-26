"""The derived vector index is embedded storage, never a Chroma HTTP server.

Explicit settings override environment-driven API selection. Public documents are
text/metadata only; all embeddings come from our bounded Embeddings adapter.
"""
from pathlib import Path


def embedded_settings(directory):
    from chromadb.config import Settings

    return Settings(
        chroma_api_impl="chromadb.api.rust.RustBindingsAPI",
        is_persistent=True,
        persist_directory=str(Path(directory).resolve()),
        anonymized_telemetry=False,
        chroma_server_host=None,
        chroma_server_http_port=None,
        chroma_client_auth_provider=None,
        chroma_server_authn_provider=None,
        chroma_server_authz_provider=None,
    )
