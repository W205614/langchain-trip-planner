import json
from types import SimpleNamespace
import pytest
from app.agent_api import rebuild as module


class Store:
    def __init__(self, **kwargs):
        self._collection=self
        self.documents=[]
    def add_documents(self, documents, **kwargs):
        self.documents.extend(documents)
    def count(self):return len(self.documents)
    def similarity_search(self, *args, **kwargs):return self.documents[:1]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    rag=SimpleNamespace(_embedding=SimpleNamespace(model="fixture"),_knowledge_store=None,_history_store=None,
        _mutation_version=7,_degraded=True,_load_knowledge_documents=lambda:[])
    monkeypatch.setattr(module,"get_rag_service",lambda:rag)
    monkeypatch.setattr(module,"CHROMA_DIR",tmp_path)
    monkeypatch.setattr(module,"Chroma",Store)
    old={"trip_knowledge":"old_knowledge","trip_history":"old_history"}
    manifest=tmp_path/"active-index.json"
    manifest.write_text(json.dumps(old),encoding="utf-8")
    return rag,manifest,old


def test_business_revision_conflict_retains_old_manifest(setup,monkeypatch):
    rag,manifest,old=setup
    monkeypatch.setattr(module,"post",lambda path,body: {"revision":10,"records":[],"documents":[]} if path.endswith("snapshot") else {"revision":11})
    with pytest.raises(RuntimeError,match="Business evidence changed"):
        module.rebuild()
    assert json.loads(manifest.read_text())==old and rag._knowledge_store is None


def test_index_mutation_conflict_retains_old_manifest(setup,monkeypatch):
    rag,manifest,old=setup
    def post(path,body):
        if path.endswith("snapshot"):return {"revision":10,"records":[],"documents":[]}
        rag._mutation_version+=1
        return {"revision":10}
    monkeypatch.setattr(module,"post",post)
    with pytest.raises(RuntimeError,match="Agent index changed"):
        module.rebuild()
    assert json.loads(manifest.read_text())==old


def test_verified_generation_switches_once(setup,monkeypatch):
    rag,manifest,old=setup
    monkeypatch.setattr(module,"post",lambda path,body: {"revision":10,"records":[],"documents":[]})
    assert module.rebuild()["success"]
    assert json.loads(manifest.read_text())!=old
    assert not rag._degraded
