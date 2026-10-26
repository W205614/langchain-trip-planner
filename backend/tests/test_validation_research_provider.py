"""Provider-only fixture checks; real Java visibility remains an HTTP E2E concern."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.services.research_evidence import check_answer_support,evidence_contract


def load(monkeypatch,enabled=True):
    monkeypatch.setenv("APP_ENV","validation" if enabled else "production")
    monkeypatch.setenv("VALIDATION_ALLOW_FIXTURES","yes")
    spec=importlib.util.spec_from_file_location("offline_research_fixture_test",Path(__file__).parents[1]/"scripts"/"validation_research_provider.py")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_research_fixture_cannot_load_in_production(monkeypatch):
    with pytest.raises(RuntimeError,match="isolated validation"):
        load(monkeypatch,enabled=False)


def test_offline_index_retains_java_visibility_metadata_and_never_embeds(monkeypatch):
    module=load(monkeypatch);index=module.FixtureIndex();seen=[];published=False
    def visibility(documents):
        seen.extend(doc.metadata for doc in documents)
        return documents if published else []
    monkeypatch.setattr(module,"visible",visibility)
    index.replace_public_knowledge_document(9,"北京","review-title",["### unique-marker\n出发前请核对景区官方公告。"],source_tier="community",document_version=3)
    assert index.retrieve_research_evidence("unique-marker","北京",local_only=True)==[]
    published=True
    evidence=index.retrieve_research_evidence("unique-marker","北京",local_only=True)
    assert evidence[0]["document_id"]==9 and evidence[0]["document_version"]==3
    assert seen and all(meta["source_type"]=="multimodal" for meta in seen)
    assert index.embedding_calls==0
    index.delete_public_knowledge_document(9)
    assert index.retrieve_research_evidence("unique-marker","北京")==[]


def test_offline_answer_is_an_actual_supported_excerpt_not_an_unchecked_fixture_conclusion(monkeypatch):
    module=load(monkeypatch)
    content="### unique-marker\n出发前请核对景区官方公告。"
    answer=module.answer_from_public_excerpt([SimpleNamespace(content="城市：北京\n公开资料：\n[1] 来源：review-title\n"+content)])
    accepted,_=evidence_contract([{"content":content,"source":"review-title","source_tier":"community","page":1}])
    assert answer=="出发前请核对景区官方公告。[1]"
    assert check_answer_support(answer,accepted)["passed"]


@pytest.mark.parametrize("heading",["#","###"])
def test_offline_provider_handles_real_prompt_collapsed_matching_markdown_title(monkeypatch,heading):
    from app.services.research_answer import _messages,build_research_answer
    from app.services import research_answer
    module=load(monkeypatch)
    title="review-title [bounded]"
    content=f"{heading} {title}\n\n出发前请核对景区官方公告。请保留预约凭证。"
    evidence=[{"content":content,"source":title,"source_tier":"reviewed","page":1}]
    accepted,_=evidence_contract(evidence)
    messages=_messages("出发前注意事项","北京",accepted,"")
    assert f"{heading} {title} 出发前请核对景区官方公告。" in messages[-1].content
    answer=module.answer_from_public_excerpt(messages)
    assert answer=="出发前请核对景区官方公告。[1]"
    assert check_answer_support(answer,accepted)["passed"]
    monkeypatch.setattr(research_answer,"get_llm",lambda **_:SimpleNamespace(
        invoke=lambda prompt:SimpleNamespace(content=module.answer_from_public_excerpt(prompt))))
    result=build_research_answer("出发前注意事项","北京",evidence)
    assert result["answer_status"]=="supported_excerpt_answer"
    assert result["generation_calls"]==1
    assert result["support_check"]["passed"]
    assert result["answer"]==answer


def test_real_prompt_supports_single_token_section_distinct_from_reviewed_title(monkeypatch):
    from app.services import research_answer
    module=load(monkeypatch)
    evidence=[{"content":"### 核验标记mvp123\n出发前请核对景区官方公告。",
               "source":"复核资料核验标记mvp123","source_tier":"reviewed","page":1}]
    accepted,_=evidence_contract(evidence)
    messages=research_answer._messages("出发前","北京",accepted,"")
    assert "### 核验标记mvp123 出发前请核对景区官方公告。" in messages[-1].content
    answer=module.answer_from_public_excerpt(messages)
    assert answer=="出发前请核对景区官方公告。[1]"
    assert check_answer_support(answer,accepted)["passed"]
    monkeypatch.setattr(research_answer,"get_llm",lambda **_:SimpleNamespace(
        invoke=lambda prompt:SimpleNamespace(content=module.answer_from_public_excerpt(prompt))))
    result=research_answer.build_research_answer("出发前","北京",evidence)
    assert result["answer_status"]=="supported_excerpt_answer"
    assert result["generation_calls"]==1
    assert result["support_check"]["passed"]
    assert result["answer"]==answer


def test_real_answer_path_falls_back_for_an_ambiguous_multiword_heading(monkeypatch):
    from app.services import research_answer
    module=load(monkeypatch)
    evidence=[{"content":"# unknown multiword heading\n\n出发前请核对景区官方公告。",
               "source":"source-title","source_tier":"reviewed","page":1}]
    accepted,_=evidence_contract(evidence)
    messages=research_answer._messages("注意事项","北京",accepted,"")
    candidate=module.answer_from_public_excerpt(messages)
    assert not check_answer_support(candidate,accepted)["passed"]
    monkeypatch.setattr(research_answer,"get_llm",lambda **_:SimpleNamespace(
        invoke=lambda prompt:SimpleNamespace(content=module.answer_from_public_excerpt(prompt))))
    result=research_answer.build_research_answer("注意事项","北京",evidence)
    assert result["answer_status"]=="extractive_fallback"
    assert not result["support_check"]["passed"]
    assert "UNSUPPORTED_CLAIM" in result["support_check"]["issues"]
    assert result["generation_calls"]==1
    assert "尚未独立核验" in result["answer"]


def test_offline_provider_never_extracts_from_second_source_with_first_citation(monkeypatch):
    from app.services.research_answer import _messages
    module=load(monkeypatch)
    accepted,_=evidence_contract([
        {"content":"# unknown-heading","source":"first","source_tier":"reviewed"},
        {"content":"出发前请核对景区官方公告。","source":"second","source_tier":"reviewed"}])
    with pytest.raises(ValueError,match="public source excerpt"):
        module.answer_from_public_excerpt(_messages("注意事项","北京",accepted,""))
