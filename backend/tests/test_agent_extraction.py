import io
import base64
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
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
        llm_api_key="test",vision_base_url="http://fixture.test",llm_base_url="http://fixture.test",vision_timeout=10,
        llm_day_max_tokens=1800,vision_reasoning_effort="low"))


def png():
    data=io.BytesIO();Image.new("RGB",(8,8),"white").save(data,format="PNG");return data.getvalue()


def test_real_image_decoder_then_one_vision_call(monkeypatch):
    wire(monkeypatch,png(),"image/png")
    calls=[]
    def model(**kwargs):
        assert kwargs["kind"]=="vision"
        def invoke(messages):
            calls.append(messages)
            return SimpleNamespace(content='{"summary":"test document","facts":["Check official opening times"]}')
        return SimpleNamespace(invoke=invoke)
    monkeypatch.setattr(extraction,"build_chat_model",model)
    result=extraction.extract({"document_id":1,"document_version":2,"city":"北京","title":"test"})
    assert len(calls)==1 and len(result["pages"])==1
    assert "Check official opening times" in result["pages"][0]


def test_declared_image_type_must_match_decoded_format(monkeypatch):
    wire(monkeypatch,png(),"image/jpeg")
    monkeypatch.setattr(extraction,"build_chat_model",lambda **kwargs:pytest.fail("No model call on invalid input"))
    with pytest.raises(ValueError,match="format"):
        extraction.extract({"document_id":1,"document_version":2,"city":"北京","title":"test"})


def test_corrupt_image_never_reaches_model(monkeypatch):
    wire(monkeypatch,b"\x89PNG\r\n\x1a\ninvalid", "image/png")
    monkeypatch.setattr(extraction,"build_chat_model",lambda **kwargs:pytest.fail("No model call on invalid input"))
    with pytest.raises(Exception):
        extraction.extract({"document_id":1,"document_version":2,"city":"北京","title":"test"})


def pdf(page_count=1, size=(32, 24)):
    import pypdfium2 as pdfium
    output = io.BytesIO()
    with pdfium.PdfDocument.new() as document:
        for _ in range(page_count):
            document.new_page(*size).close()
        document.save(output)
    return output.getvalue()


def test_real_pdf_pages_render_before_vision_calls(monkeypatch):
    wire(monkeypatch, pdf(2), "application/pdf")
    calls = []
    def invoke(messages):
        encoded = messages[1].content[1]["image_url"]["url"].split(",", 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
            assert image.format == "PNG"
            assert image.size == (48, 36)
        calls.append(messages)
        return SimpleNamespace(content='{"summary":"PDF page","facts":["Visible fact"]}')
    monkeypatch.setattr(extraction, "build_chat_model", lambda **kwargs: SimpleNamespace(invoke=invoke))
    result = extraction.extract({"document_id":1,"document_version":2,"city":"北京","title":"PDF"})
    assert len(calls) == 2
    assert len(result["pages"]) == 2
    assert "来源页: 2" in result["pages"][1]


@pytest.mark.parametrize("content, message", [
    (lambda: pdf(11), "1-10 pages"),
    (lambda: pdf(size=(4000, 4000)), "pixel budget"),
])
def test_pdf_limits_reject_before_model_call(monkeypatch, content, message):
    wire(monkeypatch, content(), "application/pdf")
    monkeypatch.setattr(extraction, "build_chat_model", lambda **kwargs: pytest.fail("No model call on invalid PDF"))
    with pytest.raises(ValueError, match=message):
        extraction.extract({"document_id":1,"document_version":2,"city":"北京","title":"PDF"})


def test_corrupt_pdf_never_reaches_model(monkeypatch):
    import pypdfium2 as pdfium
    wire(monkeypatch, b"%PDF-1.7\ninvalid", "application/pdf")
    monkeypatch.setattr(extraction, "build_chat_model", lambda **kwargs: pytest.fail("No model call on corrupt PDF"))
    with pytest.raises(pdfium.PdfiumError):
        extraction.extract({"document_id":1,"document_version":2,"city":"北京","title":"PDF"})


def test_concurrent_pdf_extractions_render_complete_pages(monkeypatch):
    wire(monkeypatch, pdf(2), "application/pdf")
    def invoke(messages):
        encoded = messages[1].content[1]["image_url"]["url"].split(",", 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
            image.load()
            assert image.size == (48, 36)
        return SimpleNamespace(content='{"summary":"PDF page","facts":[]}')
    monkeypatch.setattr(extraction, "build_chat_model", lambda **kwargs: SimpleNamespace(invoke=invoke))
    body = {"document_id":1,"document_version":2,"city":"北京","title":"PDF"}
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(extraction.extract, [body] * 8))
    assert all(len(result["pages"]) == 2 for result in results)
