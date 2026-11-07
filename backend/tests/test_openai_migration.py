"""Exercise the actual client serializer and embedding adapter at the HTTP boundary."""
import json
from types import SimpleNamespace
import httpx
import pytest
from app.services.embedding_identity import embedding_identity
from app.services.llm_service import build_chat_model
from app.services.rag_service import _OpenAICompatEmbeddings


def test_gpt_sends_supported_parameters_and_no_temperature():
    requests=[]
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={"id":"chat-test","object":"chat.completion","created":1,
            "model":"gpt-6.1-sol","choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"完成"}}],
            "usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}})
    llm=build_chat_model(model="gpt-6.1-sol",api_key="isolated-test",base_url="http://fixture/v1",
        timeout=5,max_tokens=1800,kind="text",reasoning_effort="low",temperature=0.7)
    llm.http_client.close()
    llm.http_client=httpx.Client(transport=httpx.MockTransport(respond))
    llm.client=__import__("openai").OpenAI(api_key="isolated-test",base_url="http://fixture/v1",
        http_client=llm.http_client,max_retries=0).chat.completions
    response=llm.invoke("测试")
    assert response.content=="完成"
    assert requests[0]["reasoning_effort"]=="low"
    assert requests[0]["max_completion_tokens"]==1800
    assert "temperature" not in requests[0] and "max_tokens" not in requests[0]


def test_same_model_name_from_other_provider_is_a_different_space():
    settings=SimpleNamespace(embedding_model="text-embedding-3-large",embedding_dimensions=3072,
        llm_base_url="https://api.openai.com/v1",embedding_base_url="https://old-provider.invalid/v1")
    old=embedding_identity(settings)
    settings.embedding_base_url="https://api.openai.com/v1/"
    current=embedding_identity(settings)
    assert old!=current and current["provider"]=="openai"
    assert current["endpoint"]=="https://api.openai.com/v1"


@pytest.mark.parametrize("endpoint",["https://user:secret@example.com/v1","https://example.com/v1?api_key=secret"])
def test_index_identity_never_records_credentials(endpoint):
    with pytest.raises(ValueError):
        embedding_identity(SimpleNamespace(embedding_base_url=endpoint,llm_base_url="",embedding_model="x",embedding_dimensions=3))


def test_embedding_rejects_incompatible_response_without_index_write(monkeypatch):
    client=httpx.AsyncClient
    def respond(request):
        assert json.loads(request.content)["dimensions"]==3
        return httpx.Response(200,json={"data":[{"index":0,"embedding":[0.1,0.2]}]})
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw:client(transport=httpx.MockTransport(respond),**kw))
    with pytest.raises(ValueError,match="dimension"):
        _OpenAICompatEmbeddings("fixture","http://fixture/v1","text-embedding-3-large",3).embed_query("北京")


def test_supplier_usage_reporting_survives_expired_generation_deadline(monkeypatch):
    from app.services import durable_execution,execution
    monkeypatch.setattr(execution,"remaining",lambda *_:(_ for _ in ()).throw(TimeoutError("already cancelled")))
    requests=[]
    client=httpx.Client
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={"success":True})
    monkeypatch.setattr(httpx,"Client",lambda **kw:client(transport=httpx.MockTransport(respond),**kw))
    durable_execution.report_outbound_usage({"request_key":"opaque-id","run_id":""},{"prompt_tokens":42,"completion_tokens":8})
    assert requests==[{"request_key":"opaque-id","run_id":"","input_tokens":42,"output_tokens":8}]
