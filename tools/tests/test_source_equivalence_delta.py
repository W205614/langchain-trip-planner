"""Focused source-only draft guard tests; no Docker, database or scanners."""
import copy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from tools import source_equivalence_delta as delta

NOW = datetime(2026, 10, 27, 8, tzinfo=timezone.utc)


class SourceEquivalenceDeltaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((delta.ROOT / delta.MANIFEST).read_text(encoding='utf-8'))
        cls.observed = delta.observe_sources(delta.ROOT, cls.manifest)
        cls.approval = json.loads((delta.ROOT / '.github/security-risk-acceptance.json').read_text(encoding='utf-8'))

    def test_default_disabled_and_source_preview_never_grants_acceptance(self):
        with patch.object(delta, 'observe_sources') as observation:
            with self.assertRaisesRegex(RuntimeError, 'disabled'):
                delta.review_draft()
            observation.assert_not_called()
        result = delta.review_draft(review_only=True, now=NOW)
        self.assertEqual('prepared_not_activated', result['status'])
        self.assertIs(False, result['effective_security_acceptance'])
        self.assertIs(False, result['sarif_revision_provenance_verified'])
        self.assertIsNone(result['sarif_context_comparison_count'])
        self.assertEqual('pending_fresh_inventory_and_runtime', result['agent_component_comparison'])
        self.assertEqual(self.manifest['proposed_business_tree'], result['projected_business_tree'])
        with patch.object(delta, 'ACTIVATED', True):
            with self.assertRaisesRegex(RuntimeError, 'Activation'):
                delta.review_draft(review_only=True, now=NOW)

    def test_exact_blob_scope_rejects_extra_file_same_file_bytes_modes_and_renames(self):
        delta.validate_observation(self.manifest, self.observed)
        service = next(p for p in self.manifest['files'] if p.endswith('TripLedgerService.java'))
        variants = []
        changed = copy.deepcopy(self.observed)
        changed['changed']['business-backend/src/main/java/com/tripplanner/Extra.java'] = dict(changed['changed'][service])
        variants.append(changed)
        for relative in ['backend/app.py', 'frontend/src/extra.ts', 'deploy/extra.yml', 'contracts/extra.json',
                         *self.manifest['compose_paths']]:
            changed = copy.deepcopy(self.observed)
            changed['changed'][relative] = dict(changed['changed'][service])
            variants.append(changed)
        for key, value in [('new_blob', delta.blob(b'reviewed line plus extra mutation\n')),
                           ('old_blob', '0' * 40), ('new_mode', '100755'), ('old_mode', '100755'), ('status', 'R')]:
            changed = copy.deepcopy(self.observed); changed['changed'][service][key] = value; variants.append(changed)
        changed = copy.deepcopy(self.observed); del changed['changed'][service]; variants.append(changed)
        changed = copy.deepcopy(self.observed); changed['untracked'] = ['backend/new_agent.py']; variants.append(changed)
        for variant in variants:
            with self.subTest(variant=variants.index(variant)):
                with self.assertRaisesRegex(RuntimeError, 'exact two-path'):
                    delta.validate_observation(self.manifest, variant)

    def test_other_trees_compose_and_every_frozen_record_or_validator_reject_drift(self):
        for revision in self.manifest['historical_revisions']:
            for directory in self.manifest['original_business_trees']:
                changed = copy.deepcopy(self.observed)
                changed['historical_trees'][revision][directory] = 'f' * 40
                with self.subTest(revision=revision, directory=directory), self.assertRaises(RuntimeError):
                    delta.validate_observation(self.manifest, changed)
        changed = copy.deepcopy(self.observed); changed['compose_paths'].append('docker-compose.extra.yml')
        with self.assertRaises(RuntimeError): delta.validate_observation(self.manifest, changed)
        for relative in self.manifest['frozen_files']:
            changed = copy.deepcopy(self.observed); changed['frozen_files'][relative] = '0' * 64
            with self.subTest(relative=relative), self.assertRaisesRegex(RuntimeError, 'bytes changed'):
                delta.validate_observation(self.manifest, changed)

    def test_original_expiry_cannot_be_extended_or_mismatched(self):
        policy = {'expires_at': self.manifest['expires_at']}
        delta.check_expiry(self.manifest, policy, NOW)
        with self.assertRaisesRegex(RuntimeError, 'expired'):
            delta.check_expiry(self.manifest, policy, datetime(2026, 11, 10, tzinfo=timezone.utc))
        for expiry in ['2026-11-10T23:59:59+08:00', '2026-11-08T23:59:59+08:00', '2026-11-09T23:59:59']:
            changed = dict(self.manifest, expires_at=expiry)
            with self.subTest(expiry=expiry), self.assertRaises(RuntimeError):
                delta.check_expiry(changed, policy, NOW)

    def test_complete_agent_maps_and_runtime_types_are_not_relaxed(self):
        # The positive input is a synthetic comparison fixture copied from the
        # original policy, never a claim of a fresh image/runtime observation.
        inventory = {**copy.deepcopy(self.approval['approved_image']), 'native_inventory_complete': True}
        constraints = copy.deepcopy(self.approval['runtime_required'])
        delta.validate_agent_evidence(self.approval, inventory, constraints)
        variants = []
        for key in ['packages', 'native_elf_sha256', 'application_file_sha256']:
            changed = copy.deepcopy(inventory); first = next(iter(changed[key])); changed[key][first] = 'different'; variants.append(changed)
            changed = copy.deepcopy(inventory); changed[key].pop(first); variants.append(changed)
            changed = copy.deepcopy(inventory); changed[key]['unexpected'] = 'different'; variants.append(changed)
        changed = copy.deepcopy(inventory); changed['native_inventory_complete'] = 1; variants.append(changed)
        for index, variant in enumerate(variants):
            with self.subTest(inventory_variant=index), self.assertRaises(RuntimeError):
                delta.validate_agent_evidence(self.approval, variant, constraints)
        for key, value in [('readonly_rootfs', False), ('privileged', True), ('privileged', 0),
                           ('no_new_privileges', False), ('user', '0'), ('cap_eff', '00000000a80425fb')]:
            changed = dict(constraints, **{key: value})
            with self.subTest(constraint=key, value=value), self.assertRaises(RuntimeError):
                delta.validate_agent_evidence(self.approval, inventory, changed)

    def sarif_fixture(self):
        rule = {'id': 'java/fixture', 'properties': {'security-severity': '8.8'}}
        location = {'physicalLocation': {'artifactLocation': {'uri': 'business-backend/Reviewed.java'},
                                        'region': {'startLine': 12, 'startColumn': 3}}}
        result = {'ruleId': rule['id'], 'locations': [copy.deepcopy(location)],
                  'codeFlows': [{'threadFlows': [{'locations': [{'location': {**copy.deepcopy(location),
                                                                            'message': {'text': 'reviewed receiver'}}}]}]}]}
        run = {'tool': {'driver': {'name': 'CodeQL', 'rules': [rule]}}, 'results': [result]}
        identity = delta.context.finding_identity(rule, result, run)
        policy = {'expires_at': self.manifest['expires_at'], 'entries': [{**identity, 'source_sha256': 'a' * 64,
                  'reason': 'Synthetic exact context fixture', 'classification': 'not_affected_context'}]}
        return policy, {'version': '2.1.0', 'runs': [run]}

    def test_every_raw_sarif_result_must_match_including_lower_severity_and_new_flow(self):
        policy, document = self.sarif_fixture()
        self.assertEqual(1, delta.validate_sarif_runs(policy, [document], now=NOW))
        variants = []
        for severity in ['9.8', '6.9']:
            changed = copy.deepcopy(document); changed['runs'][0]['tool']['driver']['rules'][0]['properties']['security-severity'] = severity; variants.append(changed)
        changed = copy.deepcopy(document); changed['runs'][0]['results'][0]['codeFlows'][0]['threadFlows'][0]['locations'][0]['location']['message']['text'] = 'new receiver'; variants.append(changed)
        changed = copy.deepcopy(document); changed['runs'][0]['results'][0]['locations'][0]['physicalLocation']['artifactLocation']['uri'] = 'backend/new.py'; variants.append(changed)
        changed = copy.deepcopy(document); changed['runs'][0]['results'][0]['locations'][0]['physicalLocation']['region']['startLine'] += 1; variants.append(changed)
        changed = copy.deepcopy(document); changed['runs'][0]['results'][0]['ruleId'] = 'java/new-query'; variants.append(changed)
        changed = copy.deepcopy(document); changed['runs'][0]['results'].append(copy.deepcopy(changed['runs'][0]['results'][0])); variants.append(changed)
        changed = copy.deepcopy(document); changed['runs'][0]['results'] = []; variants.append(changed)
        changed = copy.deepcopy(document); changed['runs'][0]['results'][0]['suppressions'] = [{'kind': 'external'}]; variants.append(changed)
        changed = copy.deepcopy(document); changed['runs'][0]['invocations'] = [{'executionSuccessful': False}]; variants.append(changed)
        for index, variant in enumerate(variants):
            with self.subTest(sarif_variant=index), self.assertRaises(RuntimeError):
                delta.validate_sarif_runs(policy, [variant], now=NOW)


if __name__ == '__main__':
    unittest.main()
