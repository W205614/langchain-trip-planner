import asyncio
import json
import httpx
import pytest
from app.services import call_budget, durable_execution


def test_serialized_model_request_reserves_conservative_input_and_declared_output():
    request=httpx.Request("POST","https://offline.invalid/chat/completions",json={"messages":[{"role":"user","content":"中文旅行资料"}],"max_tokens":100,"n":2})
    assert call_budget.request_token_reservation(request,"text")==len(request.content)+4096+200
    estimate=call_budget.text_token_reservation(["中文", "资料"])
    assert estimate>=len("中文资料".encode("utf-8"))+4096


@pytest.mark.parametrize("body",[{"messages":[]},{"max_tokens":-1},{"max_tokens":True},{"max_tokens":10,"n":100}])
def test_unbounded_or_invalid_model_send_is_stopped_before_transport(body,monkeypatch):
    monkeypatch.setenv("FINAL_RUN_ID","fixed-server-campaign")
    sends=[]
    with httpx.Client(transport=httpx.MockTransport(lambda request:sends.append(request) or httpx.Response(200,json={})),event_hooks=call_budget.hooks("text")) as client:
        with pytest.raises(call_budget.CallBudgetExceeded):client.post("https://offline.invalid/chat/completions",json=body)
    assert sends==[]


def test_daily_unbounded_legacy_request_keeps_compatibility_without_claiming_a_token_bound(monkeypatch):
    for key in ("FINAL_RUN_ID","FINAL_ACCEPTANCE_RUN_ID","ACCEPTANCE_BUDGET_FILE","OUTBOUND_LEDGER_REQUIRED"):
        monkeypatch.delenv(key,raising=False)
    reservations=[];sends=[]
    monkeypatch.setattr(durable_execution,"reserve_outbound",lambda kind,**kwargs:reservations.append((kind,kwargs)))
    with httpx.Client(transport=httpx.MockTransport(lambda request:sends.append(request) or httpx.Response(200,json={})),event_hooks=call_budget.hooks("text")) as client:
        client.post("https://offline.invalid/chat/completions",json={"messages":[]})
    assert len(sends)==1
    assert reservations[0][1]["reserved_tokens"] is None


def test_campaign_rejects_mcp_before_any_reservation_or_transport(monkeypatch):
    monkeypatch.setenv("FINAL_RUN_ID","fixed-server-campaign")
    reservations=[]
    monkeypatch.setattr(durable_execution,"reserve_outbound",lambda *args,**kwargs:reservations.append(kwargs))
    with pytest.raises(call_budget.CallBudgetExceeded,match="AMAP_TRANSPORT=rest"):
        call_budget.reserve("amap",map_protocol="mcp")
    assert reservations==[]


def test_actual_send_hook_centrally_reserves_tokens_without_selecting_a_campaign(monkeypatch):
    monkeypatch.setenv("FINAL_RUN_ID","fixed-server-campaign")
    monkeypatch.delenv("ACCEPTANCE_BUDGET_FILE",raising=False)
    reservations=[];sends=[]
    monkeypatch.setattr(durable_execution,"_call",lambda method,path,payload=None:reservations.append(payload) or {"reserved":True})
    with httpx.Client(transport=httpx.MockTransport(lambda request:sends.append(request) or httpx.Response(200,json={})),event_hooks=call_budget.hooks("text")) as client:
        client.post("https://offline.invalid/chat/completions",json={"messages":[],"max_tokens":80})
    assert len(sends)==len(reservations)==1
    assert reservations[0]["reserved_tokens"]==len(sends[0].content)+4096+80
    assert "run_id" not in reservations[0]


def test_failed_central_reservation_never_sends_and_async_hook_has_the_same_boundary(monkeypatch):
    monkeypatch.setenv("OUTBOUND_LEDGER_REQUIRED","true")
    monkeypatch.delenv("ACCEPTANCE_BUDGET_FILE",raising=False)
    def exhausted(*args,**kwargs):raise RuntimeError("campaign budget exhausted")
    monkeypatch.setattr(durable_execution,"_call",exhausted)
    sends=[]
    async def attempt():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:sends.append(request) or httpx.Response(200,json={})),event_hooks=call_budget.async_hooks("text")) as client:
            with pytest.raises(RuntimeError,match="budget exhausted"):
                await client.post("https://offline.invalid/chat/completions",json={"messages":[],"max_tokens":80})
    asyncio.run(attempt())
    assert sends==[]


def test_each_retry_needs_a_new_irrevocable_reservation_key(monkeypatch):
    monkeypatch.setenv("OUTBOUND_LEDGER_REQUIRED","true")
    calls=[]
    monkeypatch.setattr(durable_execution,"_call",lambda method,path,payload=None:calls.append(payload) or {"run_id":""})
    durable_execution.reserve_outbound("embedding",reserved_tokens=5000)
    durable_execution.reserve_outbound("embedding",reserved_tokens=5000)
    assert len(calls)==2 and calls[0]["request_key"]!=calls[1]["request_key"]
    assert sum(call["reserved_tokens"] for call in calls)==10000


@pytest.mark.parametrize("code", ["OUTBOUND_BUDGET_EXHAUSTED", "CAMPAIGN_BUDGET_EXHAUSTED"])
def test_java_budget_rejection_preserves_code_before_local_accounting(code, tmp_path, monkeypatch):
    import sqlite3
    ledger = tmp_path / "budget.sqlite3"
    with sqlite3.connect(ledger) as db:
        db.execute("CREATE TABLE calls(kind TEXT PRIMARY KEY,used INTEGER NOT NULL)")
        db.execute("INSERT INTO calls VALUES('amap',0)")
    monkeypatch.setenv("ACCEPTANCE_BUDGET_FILE", str(ledger))
    monkeypatch.setenv("OUTBOUND_LEDGER_REQUIRED", "true")
    attempts = []
    def rejected(method, path, payload):
        attempts.append(payload)
        httpx.Response(429, json={"code": code}, request=httpx.Request(method, "http://business-test"+path)).raise_for_status()
    monkeypatch.setattr(durable_execution, "_call", rejected)
    with pytest.raises(call_budget.CallBudgetExceeded) as error:
        call_budget.reserve("amap", map_protocol="mcp")
    assert error.value.code == code and error.value.status_code == 429
    assert len(attempts) == 1
    with sqlite3.connect(ledger) as db:
        assert db.execute("SELECT used FROM calls WHERE kind='amap'").fetchone()[0] == 0


@pytest.mark.parametrize("status,body", [(429, b'not json'), (429, b'[]'),
    (429, b'{"code":"CAPACITY_EXHAUSTED"}'), (503, b'{"code":"OUTBOUND_BUDGET_EXHAUSTED"}')])
def test_other_internal_http_errors_are_not_reclassified_as_budget(status, body, monkeypatch):
    monkeypatch.setenv("OUTBOUND_LEDGER_REQUIRED", "true")
    response = httpx.Response(status, content=body, request=httpx.Request("POST", "http://business-test/reserve"))
    original = httpx.HTTPStatusError("original HTTP failure", request=response.request, response=response)
    def rejected(*_args): raise original
    monkeypatch.setattr(durable_execution, "_call", rejected)
    with pytest.raises(httpx.HTTPStatusError) as error:
        durable_execution.reserve_outbound("amap", map_protocol="mcp")
    assert error.value is original
