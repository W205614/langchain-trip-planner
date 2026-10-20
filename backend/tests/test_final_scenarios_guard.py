import importlib.util
from pathlib import Path

import httpx
import pytest


def test_http_scenarios_refuse_nonlocal_or_unmarked_deployments(monkeypatch,tmp_path):
    source=Path(__file__).parents[1]/"scripts"/"final_scenarios.py"
    spec=importlib.util.spec_from_file_location("final_scenarios_guarded",source)
    runner=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    requests=[]
    def handler(request):
        requests.append((request.method,request.url.path))
        return httpx.Response(200,json={"offline_fixture":False})
    original=httpx.Client
    monkeypatch.setattr(runner.httpx,"Client",lambda *args,**kwargs:original(*args,transport=httpx.MockTransport(handler),**kwargs))
    with pytest.raises(RuntimeError,match="local isolated"):
        runner.run("https://external.invalid",tmp_path/"external.json")
    assert requests==[]
    with pytest.raises(RuntimeError,match="offline fixture marker"):
        runner.run("http://127.0.0.1:18080",tmp_path/"unmarked.json")
    assert requests==[("GET","/api/validation/fixture")]
