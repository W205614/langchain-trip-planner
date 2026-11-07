"""Embedding spaces are identified by provider, endpoint, model and dimensions."""
from urllib.parse import urlsplit, urlunsplit


def embedding_identity(settings):
    endpoint = settings.embedding_base_url or settings.llm_base_url or "https://api.openai.com/v1"
    url = urlsplit(endpoint)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Embedding endpoint must be an HTTP(S) base URL without credentials or query")
    endpoint = urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path.rstrip("/"), "", ""))
    return {"provider": "openai" if url.hostname.lower() == "api.openai.com" else "compatible",
            "endpoint": endpoint, "model": settings.embedding_model,
            "dimensions": settings.embedding_dimensions}
