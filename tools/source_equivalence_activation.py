"""Pinned review/activation loader. Repository review authority is the boundary.

Neither JSON nor GitHub environment variables are a human signature. The tiny
activation-pin module is reviewed separately to avoid a circular self-hash.
No CLI, environment value or manifest boolean can fill the absent approval pin.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

MODE = 'restore-response-v1'
SCOPE_PATH = '.github/source-equivalence/restore-response-v1.json'
SCOPE_SHA256 = '55b491a230a4868ebe8744ff217889cfb684c0aba553ba06a7a57ef814690428'
REVIEW_PATH = '.github/source-equivalence/restore-response-preparation-review.json'
REVIEW_SHA256 = '0c8b9746df08aa62d3899b381c939bbc6aab921aa64bb3ad45dab5416b96dfdf'
PLAN_PATH = '.github/source-equivalence/restore-response-integration-plan.json'
PREPARED_PLAN_SHA256 = 'a32ea0a58e53975e3607bfcd2c6f6ffcd32c557b0007cd43ba7fbf358e9fe4ff'  # Prepared code only; no activation grant.
ACTIVATION_PATH = '.github/source-equivalence/restore-response-activation.json'
ACTIVATION_SHA256 = 'f06880d2fede4276d87a049cf436487d8e7cc96b88e134de42fdb72f39b5e125'  # Intentionally disabled; a later independent decision is required.
DEADLINE = '2026-11-09T23:59:59+08:00'
INTEGRATION_FILES = frozenset({
    'tools/source_equivalence_delta.py', 'tools/risk_acceptance.py', 'tools/codeql_context.py',
    'tools/security_gate.py', '.github/workflows/security.yml', '.gitattributes',
    'tools/tests/test_source_equivalence_delta.py', 'tools/tests/test_source_equivalence_integration.py',
})


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def read(root, relative):
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise RuntimeError('Pinned source-equivalence evidence unavailable')
    return path.read_bytes()


def preparation(root):
    scope_raw = read(root, SCOPE_PATH).replace(b'\r\n', b'\n')
    if hashlib.sha256(scope_raw).hexdigest() != SCOPE_SHA256:
        raise RuntimeError('Exact reviewed source-equivalence scope changed')
    review = json.loads(read(root, REVIEW_PATH))
    if hashlib.sha256(canonical(review)).hexdigest() != REVIEW_SHA256 or review.get('source_gate_activation') is not False:
        raise RuntimeError('Pinned root preparation review changed; it does not grant activation')
    plan = json.loads(read(root, PLAN_PATH))
    if (PREPARED_PLAN_SHA256 is None or hashlib.sha256(canonical(plan)).hexdigest() != PREPARED_PLAN_SHA256
            or plan.get('scope_sha256') != SCOPE_SHA256 or plan.get('preparation_review_sha256') != REVIEW_SHA256
            or set(plan.get('integration_file_sha256_lf', {})) != INTEGRATION_FILES):
        raise RuntimeError('Prepared integration plan is missing or differs from reviewed scope')
    for relative, expected in plan['integration_file_sha256_lf'].items():
        if hashlib.sha256(read(root, relative).replace(b'\r\n', b'\n')).hexdigest() != expected:
            raise RuntimeError('Prepared integration code changed: ' + relative)
    return json.loads(scope_raw), plan


def require(root, mode, now=None):
    if mode != MODE or ACTIVATION_SHA256 is None:
        raise RuntimeError('Explicit source-equivalence integration is disabled; no pinned activation decision')
    _, plan = preparation(root)
    decision = json.loads(read(root, ACTIVATION_PATH))
    now = now or datetime.now(timezone.utc)
    if (hashlib.sha256(canonical(decision)).hexdigest() != ACTIVATION_SHA256
            or decision.get('mode') != MODE or decision.get('scope_sha256') != SCOPE_SHA256
            or decision.get('preparation_review_sha256') != REVIEW_SHA256
            or decision.get('integration_plan_sha256') != PREPARED_PLAN_SHA256
            or decision.get('activation_granted') is not True or decision.get('expires_at') != DEADLINE
            or decision.get('effective_scope') != 'controlled_single_host_local_demo_only'
            or now > datetime.fromisoformat(DEADLINE)):
        raise RuntimeError('Pinned activation decision missing, expired or mismatched')
    return plan
