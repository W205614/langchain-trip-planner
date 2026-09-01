"""景点图片同源代理测试，不访问高德或外部图片 CDN。"""


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


def test_photo_image_returns_not_found_without_resolved_photo(client, monkeypatch):
    monkeypatch.setattr("app.api.routes.poi._resolve_attraction_photo", lambda _name: None)

    response = client.get("/api/poi/photo/image", params={"name": "暂无图片景点"})

    assert response.status_code == 404


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
