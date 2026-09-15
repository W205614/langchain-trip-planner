"""General photo fallback, all provider calls replaced by offline fixtures."""
from types import SimpleNamespace

import pytest

from app.services import poi_photos as poi


@pytest.fixture(autouse=True)
def clean_photo_caches(monkeypatch):
    caches = [poi._photo_records, poi._photo_results, poi._photo_failures]
    for cache in caches:
        cache.clear()
    monkeypatch.setattr(poi, "_PHOTO_MIN_INTERVAL", 0)
    monkeypatch.setattr(poi, "_resolve_attraction_photo", lambda _: pytest.fail("unverified name fallback"))
    yield
    for cache in caches:
        cache.clear()


def setup_provider(monkeypatch, parents=None, child_photos=None):
    child = {"id": "CHILD", "name": "青山文化景区纪念堂", "cityname": "西安市",
             "location": "108.1,34.1", "photos": child_photos or []}
    parent = {"id": "PARENT", "name": "青山文化景区", "cityname": "西安市",
              "location": "108.101,34.101", "photos": [{"url": "https://test/parent"}]}
    calls = []
    def detail(pid):
        calls.append(("detail", pid))
        return child if pid == "CHILD" else parent
    def search(path, params):
        assert params["citylimit"] == "true" and params["offset"] == 3
        assert params["city"] == "西安" and params["keywords"] == "青山文化景区"
        calls.append(("search", path))
        return {"pois": parents if parents is not None else [parent]}
    monkeypatch.setattr(poi, "get_amap_service", lambda: SimpleNamespace(get_poi_detail=detail, _get=search))
    monkeypatch.setattr(poi, "_download_photo", lambda url: (b"fixture-raster", "image/jpeg"))
    return child, parent, calls


def render():
    return poi.render_attraction_photo("青山文化景区纪念堂", "CHILD", "西安")


def test_parent_reference_is_visible_and_cached(monkeypatch):
    _, _, calls = setup_provider(monkeypatch)
    result = render()
    assert result.media_type == "image/svg+xml"
    assert "景区参考图（非该具体点位实拍）" in result.body.decode()
    assert "data:image/jpeg;base64," in result.body.decode()
    assert result.headers["cache-control"] == "no-store"
    assert render().body == result.body
    assert len(calls) == 2


@pytest.mark.parametrize("field,value", [("cityname", "北京市"), ("location", "116.4,39.9"),
    ("location", ""), ("location", "nan,34.1"), ("name", "青山文化景区售票处")])
def test_unrelated_or_unverifiable_parent_is_rejected(monkeypatch, field, value):
    parents = []
    _, parent, _ = setup_provider(monkeypatch, parents=parents)
    parents.append({**parent, field: value})
    assert "景点图片暂不可用" in render().body.decode()


def test_ambiguous_parents_rejected(monkeypatch):
    parents = []
    _, parent, _ = setup_provider(monkeypatch, parents=parents)
    parents.extend([parent, {**parent, "id": "OTHER"}])
    assert "景点图片暂不可用" in render().body.decode()


def test_exact_photo_tries_alternate_before_parent(monkeypatch):
    _, _, calls = setup_provider(monkeypatch, child_photos=[{"url": "bad"}, {"url": "good"}])
    downloads = []
    def download(url):
        downloads.append(url)
        return (b"exact", "image/png") if url == "good" else None
    monkeypatch.setattr(poi, "_download_photo", download)
    assert render().body == b"exact"
    assert downloads == ["bad", "good"] and len(calls) == 1


def test_missing_all_photos_short_cached_placeholder(monkeypatch):
    _, parent, calls = setup_provider(monkeypatch)
    parent["photos"] = []
    assert "景点图片暂不可用" in render().body.decode()
    count = len(calls)
    render()
    assert len(calls) == count
    assert poi._photo_results[next(iter(poi._photo_results))][0] <= poi.time.monotonic() + 30


def test_provider_failure_is_placeholder_and_deadline_restored(monkeypatch):
    from app.services.execution import deadline_var
    monkeypatch.setattr(poi, "get_amap_service", lambda: (_ for _ in ()).throw(TimeoutError()))
    before = deadline_var.get()
    assert "景点图片暂不可用" in render().body.decode()
    assert deadline_var.get() == before


def test_failure_cache_and_positive_cache_have_bounds(monkeypatch):
    for n in range(300):
        poi._cache_put(poi._photo_failures, str(n), True, 30)
    assert len(poi._photo_failures) == 256
    assert poi._cache_get(poi._photo_failures, "0") is None


def test_reference_rejects_svg_payload_and_escapes_title():
    assert poi._reference_photo((b"<svg/>", "image/svg+xml"), "x") is None
    result = poi._reference_photo((b"image", "image/png"), "<script>")
    assert b"<script>" not in result[0] and b"&lt;script&gt;" in result[0]
