"""Focused disabled-hook, real-commit-observation and language-provenance cases."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from tools.tests.legacy_review_fixture import checkout
from tools import source_equivalence_activation as activation
from tools import source_equivalence_delta as delta
from tools import codeql_context as context, risk_acceptance as risk, security_gate as gate

NOW = datetime(2026, 10, 27, 8, tzinfo=timezone.utc)


class SourceEquivalenceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = checkout(cls, delta.ROOT)
        cls.manifest = json.loads((cls.root / delta.MANIFEST).read_text(encoding='utf-8'))

    def observation(self):
        entry = {'source.java': ['100644', 'a' * 40]}
        return {'head': 'b' * 40, 'baseline_is_ancestor': True,
                'candidate_trees': dict(self.manifest['original_business_trees'], **{'business-backend': self.manifest['proposed_business_tree']}),
                'compose_paths': self.manifest['compose_paths'], 'committed_compose_paths': self.manifest['compose_paths'],
                'committed_changes': copy.deepcopy(self.manifest['files']), 'status': '',
                'head_entries': entry, 'index_entries': copy.deepcopy(entry),
                'head_worktree_blobs': {'source.java': 'a' * 40}, 'actual_worktree_blobs': {'source.java': 'a' * 40}}

    @patch.object(activation, 'ACTIVATION_SHA256', None)
    def test_disabled_hooks_cannot_be_enabled_by_mode_or_manifest_and_no_scanner_runs(self):
        with self.assertRaisesRegex(RuntimeError, 'disabled'):
            activation.require(self.root, activation.MODE, NOW)
        with self.assertRaisesRegex(RuntimeError, 'disabled'):
            delta.validate_release(self.root, activation.MODE, NOW)
        with patch.object(gate, 'tool') as scanner:
            with self.assertRaisesRegex(RuntimeError, 'disabled'):
                gate.image(Path('unused'), 'no-image', source_mode=activation.MODE)
            scanner.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'disabled'):
            gate.sarif(Path('unused'), {'entries': []}, source_mode=activation.MODE, language='python')
        with self.assertRaisesRegex(RuntimeError, 'disabled'):
            context.load(self.root / '.github/codeql-context-assessments.json', self.root, NOW, source_mode=activation.MODE)
        approval = risk.load(self.root / '.github/security-risk-acceptance.json', self.root, NOW)
        with self.assertRaisesRegex(RuntimeError, 'disabled'):
            risk.validate_sources(approval, self.root, risk.APPROVED_REVISION, source_mode=activation.MODE)

    def test_real_head_index_and_worktree_are_separate_and_restoration_cannot_hide_drift(self):
        observation = self.observation()
        delta.validate_release_observation(self.manifest, observation)
        variants = []
        changed = copy.deepcopy(observation); changed['baseline_is_ancestor'] = False; variants.append(changed)
        changed = copy.deepcopy(observation); changed['candidate_trees']['business-backend'] = 'f' * 40; variants.append(changed)
        changed = copy.deepcopy(observation); next(iter(changed['committed_changes'].values()))['new_blob'] = 'f' * 40; variants.append(changed)
        changed = copy.deepcopy(observation); changed['index_entries']['source.java'][1] = 'f' * 40; variants.append(changed)
        changed = copy.deepcopy(observation); changed['actual_worktree_blobs']['source.java'] = 'f' * 40; variants.append(changed)
        changed = copy.deepcopy(observation); changed['status'] = ' M source.java'; variants.append(changed)
        changed = copy.deepcopy(observation); changed['compose_paths'] = [*changed['compose_paths'], 'docker-compose.extra.yml']; variants.append(changed)
        changed = copy.deepcopy(observation); changed['committed_compose_paths'] = [*changed['committed_compose_paths'], 'docker-compose.extra.yml']; variants.append(changed)
        for index, variant in enumerate(variants):
            with self.subTest(variant=index), self.assertRaises(RuntimeError):
                delta.validate_release_observation(self.manifest, variant)

    def test_preparation_pin_and_actual_current_hook_hashes_reject_mutation(self):
        scope, plan = activation.preparation(self.root)
        self.assertEqual(self.manifest, scope)
        self.assertEqual(activation.INTEGRATION_FILES, set(plan['integration_file_sha256_lf']))
        original_read = activation.read
        for relative in [activation.SCOPE_PATH, activation.REVIEW_PATH, activation.PLAN_PATH, 'tools/security_gate.py']:
            def altered(root, path, target=relative):
                raw = original_read(root, path)
                return raw.replace(b'"project": "Trip"', b'"project": "Changed"') if path == target == activation.REVIEW_PATH else raw + b' ' if path == target else raw
            # A JSON trailing space is semantically identical and intentionally
            # irrelevant to canonical plan/review pins; change a real value.
            if relative == activation.PLAN_PATH:
                def altered(root, path):
                    raw = original_read(root, path)
                    return raw.replace(b'"repository":', b'"changed_repository":', 1) if path == activation.PLAN_PATH else raw
            with self.subTest(relative=relative), patch.object(activation, 'read', side_effect=altered), self.assertRaises(RuntimeError):
                activation.preparation(self.root)

    def test_synthetic_activation_pin_cannot_broaden_scope_review_plan_or_expiry(self):
        import hashlib
        decision = {'mode': activation.MODE, 'scope_sha256': activation.SCOPE_SHA256,
                    'preparation_review_sha256': activation.REVIEW_SHA256,
                    'integration_plan_sha256': activation.PREPARED_PLAN_SHA256,
                    'activation_granted': True, 'expires_at': activation.DEADLINE,
                    'effective_scope': 'controlled_single_host_local_demo_only'}
        original_read = activation.read
        def attempt(record, now=NOW, wrong_hash=False):
            raw = activation.canonical(record)
            pinned = '0' * 64 if wrong_hash else hashlib.sha256(raw).hexdigest()
            def fixture_read(root, relative):
                return raw if relative == activation.ACTIVATION_PATH else original_read(root, relative)
            with patch.object(activation, 'ACTIVATION_SHA256', pinned), patch.object(activation, 'read', side_effect=fixture_read):
                return activation.require(self.root, activation.MODE, now)
        self.assertEqual(activation.SCOPE_SHA256, attempt(decision)['scope_sha256'])
        for key, value in [('mode', 'unknown'), ('scope_sha256', '0' * 64), ('preparation_review_sha256', '0' * 64),
                           ('integration_plan_sha256', '0' * 64), ('activation_granted', False),
                           ('expires_at', '2026-11-10T23:59:59+08:00'), ('effective_scope', 'public_production')]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                attempt(dict(decision, **{key: value}))
        with self.assertRaises(RuntimeError): attempt(decision, wrong_hash=True)
        with self.assertRaises(RuntimeError): attempt(decision, datetime(2026, 11, 10, tzinfo=timezone.utc))

    def synthetic_policy(self):
        rule = {'id': 'py/fixture', 'properties': {'security-severity': '8.8'}}
        result = {'ruleId': rule['id'], 'locations': [{'physicalLocation': {'artifactLocation': {'uri': 'backend/fixture.py'}, 'region': {'startLine': 1}}}]}
        run = {'tool': {'driver': {'name': 'CodeQL', 'rules': [rule]}}, 'results': [result],
               'automationDetails': {'id': '/language:python/'}, 'invocations': [{'executionSuccessful': True}]}
        identity = context.finding_identity(rule, result, run)
        entry = {**identity, 'source_sha256': 'a' * 64, 'reason': 'Synthetic provenance fixture'}
        return {'expires_at': activation.DEADLINE, 'entries': [entry]}, {'version': '2.1.0', 'runs': [run]}

    def test_codeql_requires_successful_invocation_category_and_exact_language_subset(self):
        policy, document = self.synthetic_policy()
        with patch.dict(delta.LANGUAGE_CONTEXTS, {'python': ('py/', 1)}):
            self.assertEqual(1, delta.codeql_language_context(policy, [document], 'python', NOW))
            for change in ['missing_invocation', 'failed_invocation', 'wrong_category', 'new_flow', 'lower_severity', 'missing_result']:
                variant = copy.deepcopy(document); run = variant['runs'][0]
                if change == 'missing_invocation': run.pop('invocations')
                if change == 'failed_invocation': run['invocations'][0]['executionSuccessful'] = False
                if change == 'wrong_category': run['automationDetails']['id'] = '/language:java-kotlin/'
                if change == 'new_flow': run['results'][0]['codeFlows'] = [{'message': {'text': 'new flow'}, 'threadFlows': []}]
                if change == 'lower_severity': run['tool']['driver']['rules'][0]['properties']['security-severity'] = '6.9'
                if change == 'missing_result': run['results'] = []
                with self.subTest(change=change), self.assertRaises(RuntimeError):
                    delta.codeql_language_context(policy, [variant], 'python', NOW)
        with self.assertRaisesRegex(RuntimeError, 'subset'):
            delta.codeql_language_context(policy, [document], 'python', NOW)
        with self.assertRaisesRegex(RuntimeError, 'documents unavailable'):
            delta.codeql_language_context({'entries': [], 'expires_at': activation.DEADLINE}, [], 'actions', NOW)

    def test_codeql_job_identity_requires_current_sha_repository_run_and_analysis_success(self):
        plan = {'repository': 'fixture/project'}; head = 'a' * 40
        environment = {'GITHUB_ACTIONS': 'true', 'GITHUB_SHA': head, 'GITHUB_REPOSITORY': 'fixture/project',
                       'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_JOB': 'codeql',
                       'GITHUB_WORKFLOW_REF': 'fixture/project/.github/workflows/security.yml@refs/heads/main',
                       'TRIP_CODEQL_ANALYZE_OUTCOME': 'success'}
        with patch.dict(os.environ, environment, clear=True):
            result = delta.codeql_environment(plan, head, 'python')
            self.assertEqual('/language:python/', result['category'])
            for key, value in [('GITHUB_SHA', 'b' * 40), ('GITHUB_REPOSITORY', 'wrong/project'), ('GITHUB_RUN_ID', ''),
                               ('GITHUB_RUN_ATTEMPT', '0'), ('GITHUB_JOB', 'other'), ('TRIP_CODEQL_ANALYZE_OUTCOME', 'failure')]:
                with self.subTest(key=key), patch.dict(os.environ, {key: value}), self.assertRaises(RuntimeError):
                    delta.codeql_environment(plan, head, 'python')

    def test_receipt_rejects_raw_mutation_other_job_old_run_stale_or_whole22_claim(self):
        identity = {'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '123', 'language': 'python'}
        hashes = {'python.sarif': 'b' * 64}
        plan = {'integration_file_sha256_lf': {'tools/source_equivalence_delta.py': 'c' * 64,
                                             '.github/workflows/security.yml': 'd' * 64}}
        receipt = {'schema_version': 1, 'checked_at': NOW.isoformat(), 'job_identity': identity,
                   'raw_sarif_sha256': hashes, 'producer_sha256_lf': 'c' * 64, 'workflow_sha256_lf': 'd' * 64,
                   'scope_complete_for_language': True, 'whole_22_verified': False}
        delta.validate_codeql_receipt(receipt, identity, hashes, plan, NOW)
        variants = []
        for key, value in [('checked_at', '2026-10-26T08:00:00+00:00'), ('producer_sha256_lf', 'e' * 64),
                           ('workflow_sha256_lf', 'e' * 64), ('whole_22_verified', True), ('scope_complete_for_language', False)]:
            changed = copy.deepcopy(receipt); changed[key] = value; variants.append(changed)
        changed = copy.deepcopy(receipt); changed['raw_sarif_sha256']['python.sarif'] = 'e' * 64; variants.append(changed)
        for key, value in [('GITHUB_SHA', 'e' * 40), ('GITHUB_RUN_ID', '122'), ('language', 'java-kotlin')]:
            changed = copy.deepcopy(receipt); changed['job_identity'][key] = value; variants.append(changed)
        for index, variant in enumerate(variants):
            with self.subTest(variant=index), self.assertRaises(RuntimeError):
                delta.validate_codeql_receipt(variant, identity, hashes, plan, NOW)

    def test_workflow_prepares_same_job_receipt_and_exact_fixed_mode_without_extra_jobs(self):
        workflow = (self.root / '.github/workflows/security.yml').read_text(encoding='utf-8')
        job = workflow.split('  codeql:\n', 1)[1].split('\n  image-scan:', 1)[0]
        self.assertLess(job.index('id: codeql_analyze'), job.index('--record-codeql-job'))
        self.assertLess(job.index('--record-codeql-job'), job.index('security_gate.py sarif'))
        self.assertIn('--source-equivalence restore-response-v1 --codeql-language ${{ matrix.language }}', job)
        self.assertEqual(2, job.count('TRIP_CODEQL_ANALYZE_OUTCOME: ${{ steps.codeql_analyze.outcome }}'))
        self.assertEqual(4, len(re.findall(r'^          - language:', job, flags=re.MULTILINE)))
        self.assertEqual(['secret-scan', 'runtime-image-scan', 'dependency-scan', 'codeql', 'image-scan'],
                         re.findall(r'^  ([a-z][\w-]*):$', workflow.split('\njobs:\n', 1)[1], flags=re.MULTILINE))


if __name__ == '__main__':
    unittest.main()
