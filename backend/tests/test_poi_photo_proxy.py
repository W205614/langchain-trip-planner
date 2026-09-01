"""景点图片同源代理测试，不访问高德或外部图片 CDN。"""

import socket

from app.api.routes import poi


def test_photo_image_returns_same_origin_image(client, monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.poi._resolve_attraction_photo",
        lambda _name: "https://images.example.test/tiantan.webp",
    )
    monkeypatch.setattr(
        "app.api.routes.poi._download_photo",
        lambda _url: (b"webp-image-bytes", "image/webp"),
    )

    response = client.get("/api/poi/photo/image", params={"name": "天坛公园"})

    assert response.status_code == 200
    assert response.content == b"webp-image-bytes"
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "public, max-age=3600"


def test_photo_image_returns_placeholder_without_resolved_photo(client, monkeypatch):
    monkeypatch.setattr("app.api.routes.poi._resolve_attraction_photo", lambda _name: None)

    response = client.get("/api/poi/photo/image", params={"name": "暂无图片景点"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"


def test_photo_resolution_is_cached(monkeypatch):
    poi._photo_url_cache.clear()
    monkeypatch.setattr("app.api.routes.poi.get_amap_service", lambda: object())
    calls = []

    def _fake_lookup(_service, name):
        calls.append(name)
        return "https://images.example.test/tiantan.webp"

    monkeypatch.setattr("app.api.routes.poi._rate_limited_photo_call", _fake_lookup)
    try:
        assert poi._resolve_attraction_photo("天坛公园") == "https://images.example.test/tiantan.webp"
        assert poi._resolve_attraction_photo("天坛公园") == "https://images.example.test/tiantan.webp"
        assert calls == ["天坛公园"]
    finally:
        poi._photo_url_cache.clear()


def test_photo_proxy_rejects_private_network_addresses(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
    )

    assert poi._is_safe_remote_url("http://images.example.test/private.jpg") is False


def test_photo_image_returns_placeholder_when_remote_cdn_fails(client, monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.poi._resolve_attraction_photo",
        lambda _name: "https://images.example.test/unavailable.jpg",
    )
    monkeypatch.setattr("app.api.routes.poi._download_photo", lambda _url: None)

    response = client.get("/api/poi/photo/image", params={"name": "天坛公园"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert "天坛公园" in response.text


def test_photo_image_rejects_empty_name(client):
    response = client.get("/api/poi/photo/image", params={"name": ""})

    assert response.status_code == 422
