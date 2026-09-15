import io
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from PIL import Image
from app.agent_api import extraction


def wire(monkeypatch, content, media):
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        @contextmanager
        def stream(self, *args, **kwargs):
            yield SimpleNamespace(headers={"content-type":media},raise_for_status=lambda:None,iter_bytes=lambda:iter([content]))
    monkeypatch.setattr(extraction.httpx,"Client",Client)
    monkeypatch.setenv("BUSINESS_URL","http://java.test")
    monkeypatch.setenv("INTERNAL_SERVICE_KEY","test-only-012345678901234567890123")
    monkeypatch.setattr(extraction,"get_settings",lambda:SimpleNamespace(vision_model="fixture-vision",vision_api_key="test",
        llm_api_key="test",vision_base_url="http://fixture.test",llm_base_url="http://fixture.test",vision_timeout=10))


def png():
    data=io.BytesIO();Image.new("RGB",(8,8),"white").save(data,format="PNG");return data.getvalue()


def test_real_image_decoder_then_one_vision_call(monkeypatch):
    wire(monkeypatch,png(),"image/png")
    calls=[]
    def model(**kwargs):
        assert kwargs["max_retries"]==0
        def invoke(messages):
            calls.append(messages)
            return SimpleNamespace(content='{"summary":"test document","facts":["Check official opening times"]}')
        return SimpleNamespace(invoke=invoke)
    monkeypatch.setattr(extraction,"ChatOpenAI",model)
    result=extraction.extract({"document_id":1,"document_version":2,"city":"北京","title":"test"})
    assert len(calls)==1 and len(result["pages"])==1
    assert "Check official opening times" in result["pages"][0]


def test_declared_image_type_must_match_decoded_format(monkeypatch):
    wire(monkeypatch,png(),"image/jpeg")
    monkeypatch.setattr(extraction,"ChatOpenAI",lambda **kwargs:pytest.fail("No model call on invalid input"))
    with pytest.raises(ValueError,match="format"):
        extraction.extract({"document_id":1,"document_version":2,"city":"北京","title":"test"})


def test_corrupt_image_never_reaches_model(monkeypatch):
    wire(monkeypatch,b"\x89PNG\r\n\x1a\ninvalid", "image/png")
    monkeypatch.setattr(extraction,"ChatOpenAI",lambda **kwargs:pytest.fail("No model call on invalid input"))
    with pytest.raises(Exception):
        extraction.extract({"document_id":1,"document_version":2,"city":"北京","title":"test"})
