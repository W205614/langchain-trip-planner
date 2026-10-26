"""Deterministic evidence and quotation checks, never a semantic truth evaluator.

The check deliberately rejects paraphrases it cannot establish from a cited
excerpt. A matching quotation proves textual support, not the source's truth.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

TIERS = {"official", "reviewed", "community", "curated_static", "amap_live", "unknown"}


def _timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def evidence_contract(items: list[dict], now=None) -> tuple[list[dict], list[dict]]:
    now = now or datetime.now(timezone.utc)
    accepted, rejected = [], []
    for position, raw in enumerate(items[:8], 1):
        if not isinstance(raw, dict):
            rejected.append({"position": position, "reason": "INVALID_EVIDENCE"})
            continue
        item = dict(raw)
        content, source = str(item.get("content") or "").strip(), str(item.get("source") or "").strip()
        page, tier = item.get("page"), str(item.get("source_tier") or "unknown")
        if not content or not source or len(source) > 256 or tier not in TIERS:
            rejected.append({"position": position, "reason": "INVALID_SOURCE_METADATA"})
            continue
        if page is not None and (not isinstance(page, int) or isinstance(page, bool) or page < 1):
            rejected.append({"position": position, "reason": "INVALID_PAGE"})
            continue
        expiry = _timestamp(item.get("expires_at"))
        observed = _timestamp(item.get("observed_at") or item.get("verified_at") or item.get("updated_at"))
        invalid_time = bool(item.get("expires_at") and expiry is None)
        invalid_time |= bool((item.get("observed_at") or item.get("verified_at") or item.get("updated_at")) and observed is None)
        demo = any(item.get(key) in (True, 1, "true", "yes", "demo", "fixture") for key in ("demo", "is_demo", "is_fixture"))
        demo |= item.get("source_type") in {"demo", "fixture", "synthetic"}
        demo |= any(marker in source or marker in content for marker in ("演示数据", "示例数据", "合成数据", "离线样例"))
        if item.get("data_classification") in {"demonstration", "mixed"}:
            demo = True
        freshness = "invalid_timestamp" if invalid_time else "expired" if expiry and expiry <= now else "dated" if observed else "unknown"
        item.update(content=content[:6000], source=source, source_tier=tier, page=page,
                    freshness=freshness, data_classification="demonstration" if demo else "live_or_curated",
                    source_tier_assurance="declared_metadata_not_independently_verified",
                    eligible_for_answer=not demo and freshness not in {"expired", "invalid_timestamp"})
        accepted.append(item)
    return accepted, rejected


def _normalized(value):
    # Keep signs, decimal points, percentages and numeric separators: removing
    # punctuation would incorrectly equate 1.0 with 10 or -10 with 10.
    return re.sub(r"[\s，。！？；、\u201c\u201d\u2018\u2019（）()《》【】\"']", "", value).casefold()


def _complete_units(content):
    return {_normalized(re.sub(r"^[ #*+\-]+", "", unit))
            for unit in re.split(r"[。！？\n]+", content) if unit.strip()}


def check_answer_support(answer: str, evidence: list[dict]) -> dict:
    references = [int(value) for value in re.findall(r"\[(\d+)\]", answer)]
    invalid = sorted({index for index in references if not 1 <= index <= len(evidence)})
    problems = []
    if invalid:
        problems.append("INVALID_CITATION")
    # Providers commonly put references after the sentence punctuation.
    attached = re.sub(r"([。！？])((?:\s*\[\d+\])+)", r"\2\1", answer)
    claims = [part.strip() for part in re.split(r"(?<=[。！？])|\n+", attached) if part.strip()]
    for claim in claims:
        citations = [int(value) for value in re.findall(r"\[(\d+)\]", claim)]
        text = _normalized(re.sub(r"\[\d+\]", "", claim))
        if not text:
            continue
        if not citations:
            problems.append("UNCITED_CLAIM")
            continue
        valid = [evidence[index - 1] for index in citations if 1 <= index <= len(evidence)]
        # Whole sentence/line equality prevents dropping a negation or a price
        # qualifier from a larger source sentence while retaining a substring.
        if not valid or not any(item.get("eligible_for_answer", True) and text in _complete_units(item["content"]) for item in valid):
            problems.append("UNSUPPORTED_CLAIM")
    if not references or not claims:
        problems.append("MISSING_CITATIONS")
    return {"passed": not problems, "method": "conservative_exact_excerpt_match",
            "issues": sorted(set(problems)), "independent_semantic_evaluation": False,
            "boundary": "Checks citation range and textual support only; does not prove factual truth, relevance or current validity."}


def retrieval_result(query: str, city: str, evidence: list[dict]) -> dict:
    accepted, rejected = evidence_contract(evidence)
    return {"city": city, "query": query, "mode": "retrieval_only", "evidence": accepted,
            "rejected_evidence": rejected, "answer": None, "answer_status": "not_generated",
            "generation_calls": 0, "retrieval_method": "local_lexical_existing_index",
            "boundary": "Only existing public index excerpts; no generation, embedding or city-index build in this request."}
