"""Exact contextual review must retain raw highs and block changed/new evidence."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import codeql_context as context
from tools import security_gate as gate

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)


class CodeqlContextTests(unittest.TestCase):
    def fixture(self, root):
        source = root / 'business-backend/Reviewed.java'; source.parent.mkdir()
        source.write_bytes(b'class Reviewed {}\n'); source_hash = context.digest(source.read_bytes())
        result = {'ruleId': 'java/reviewed', 'locations': [{'physicalLocation': {
            'artifactLocation': {'uri': 'business-backend/Reviewed.java', 'index': 0},
            'region': {'startLine': 1, 'startColumn': 1}}}],
            'codeFlows': [{'threadFlows': [{'locations': [{'location': {
                'physicalLocation': {'artifactLocation': {'uri': 'business-backend/Reviewed.java', 'index': 0},
                                     'region': {'startLine': 1}}, 'message': {'text': 'receiver-specific operation'}}}]}]}]}
        rule = {'id': 'java/reviewed', 'properties': {'security-severity': '8.8'}}
        run = {'tool': {'driver': {'name': 'CodeQL', 'rules': [rule]}}, 'results': [result],
               'artifacts': [{'location': {'uri': 'business-backend/Reviewed.java'}}, {'location': {'uri': 'business-backend/Other.java'}}]}
        identity = context.finding_identity(rule, result, run)
        review = {'sarif_source_revision': 'd' * 40, 'supporting_source_bindings_lf': {'business-backend/Reviewed.java': source_hash},
                  'findings': [{**identity, 'raw_trace_sha256': 'e' * 64, 'source_sha256_current_worktree_lf': source_hash}]}
        review_path = root / 'review.json'; review_path.write_text(json.dumps(review))
        policy = {'schema_version': 1, 'classification': 'not_affected_context', 'expires_at': '2026-11-09T23:59:59+08:00',
                  'flow_hash_algorithm': context.FLOW_ALGORITHM, 'review_record': 'review.json',
                  'review_record_sha256': context.digest(review_path.read_bytes()), 'reviewed_business_revision': 'a' * 40,
                  'business_tree_bindings': {p: 'c' * 40 for p in context.BUSINESS_DIRECTORIES},
                  'source_bindings': review['supporting_source_bindings_lf'],
                  'entries': [{**identity, 'source_sha256': source_hash, 'reviewed_raw_trace_sha256': 'e' * 64,
                               'classification': 'not_affected_context', 'reason': 'Exact reviewed fixture receiver context'}]}
        policy_path = root / 'context.json'; policy_path.write_text(json.dumps(policy))
        sarif_dir = root / 'sarif'; sarif_dir.mkdir(); (sarif_dir / 'test.sarif').write_text(json.dumps({'runs': [run]}))
        return policy_path, policy, run, source, sarif_dir

    def fake_git(self, root, *args):
        if args == ('rev-parse', 'HEAD'): return 'b' * 40
        if args[0] == 'status': return ''
        return 'c' * 40

    def test_default_blocks_exact_explicit_context_preserves_raw_score_and_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path, policy, run, _, directory = self.fixture(root)
            with patch.object(context, 'EXPECTED_ENTRY_COUNT', 1), patch.object(context, 'git', side_effect=self.fake_git):
                loaded = context.load(path, root, NOW)
            default = gate.sarif(directory)[0]; self.assertEqual('blocked_findings', default['status'])
            accepted = gate.sarif(directory, loaded)[0]
            self.assertEqual('pass_codeql_context', accepted['status']); self.assertEqual(1, accepted['raw_high_critical_findings'])
            self.assertEqual(1, accepted['not_affected_context_findings']); self.assertEqual(0, accepted['blocking_findings'])
            self.assertEqual('8.8', accepted['context_assessments'][0]['security_severity'])
            self.assertEqual('not_affected_context', accepted['context_assessments'][0]['classification'])

    def test_changed_flow_new_query_severity_or_duplicate_result_cannot_be_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); _, policy, run, _, directory = self.fixture(root)
            variants = []
            changed = copy.deepcopy(run); changed['results'][0]['codeFlows'][0]['threadFlows'][0]['locations'][0]['location']['message']['text'] = 'different consumer'; variants.append(changed)
            changed = copy.deepcopy(run); changed['results'][0]['ruleId'] = 'java/new'; changed['tool']['driver']['rules'][0]['id'] = 'java/new'; variants.append(changed)
            changed = copy.deepcopy(run); changed['tool']['driver']['rules'][0]['properties']['security-severity'] = '9.8'; variants.append(changed)
            changed = copy.deepcopy(run); changed['results'].append(copy.deepcopy(changed['results'][0])); variants.append(changed)
            for variant in variants:
                (directory / 'test.sarif').write_text(json.dumps({'runs': [variant]}))
                self.assertEqual('blocked_findings', gate.sarif(directory, policy)[0]['status'])

    def test_source_tree_review_expiry_and_missing_proof_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path, policy, _, source, _ = self.fixture(root)
            with patch.object(context, 'EXPECTED_ENTRY_COUNT', 1), patch.object(context, 'git', side_effect=self.fake_git):
                with self.assertRaises(RuntimeError): context.load(path, root, datetime(2026, 11, 10, tzinfo=timezone.utc))
                source.write_bytes(b'changed source\n')
                with self.assertRaises(RuntimeError): context.load(path, root, NOW)
                source.write_bytes(b'class Reviewed {}\n')
                changed = copy.deepcopy(policy); del changed['entries'][0]['semantic_flow_sha256']; path.write_text(json.dumps(changed))
                with self.assertRaises(RuntimeError): context.load(path, root, NOW)
                path.write_text(json.dumps(policy)); (root / 'review.json').write_text('changed review')
                with self.assertRaises(RuntimeError): context.load(path, root, NOW)
            def moved(root, *args):
                return 'f' * 40 if args == ('rev-parse', 'b' * 40 + ':backend') else self.fake_git(root, *args)
            # Restore a separate intact fixture to distinguish tree drift from a
            # changed review-file failure.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path, _, _, _, _ = self.fixture(root)
            with patch.object(context, 'EXPECTED_ENTRY_COUNT', 1), patch.object(context, 'git', side_effect=moved):
                with self.assertRaises(RuntimeError): context.load(path, root, NOW)

    def test_flow_normalizes_artifact_numbering_but_not_uri_or_step_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, run, _, _ = self.fixture(Path(tmp)); result = run['results'][0]
            before = context.semantic_flow_hash(result, run)
            moved = copy.deepcopy(run); moved['artifacts'].reverse()
            moved['results'][0]['codeFlows'][0]['threadFlows'][0]['locations'][0]['location']['physicalLocation']['artifactLocation']['index'] = 1
            moved['results'][0]['codeFlows'][0]['id'] = 'new-generated-flow-id'
            moved['results'][0]['codeFlows'][0]['threadFlows'][0]['id'] = 'new-generated-thread-id'
            moved['results'][0]['codeFlows'][0]['threadFlows'][0]['locations'][0]['location']['physicalLocation']['artifactLocation']['uriBaseId'] = '%SRCROOT%'
            self.assertEqual(before, context.semantic_flow_hash(moved['results'][0], moved))
            bad = copy.deepcopy(result); bad['codeFlows'][0]['threadFlows'][0]['locations'][0]['location']['physicalLocation']['artifactLocation']['index'] = 1
            with self.assertRaises(RuntimeError): context.semantic_flow_hash(bad, run)


if __name__ == '__main__':
    unittest.main()
