"""SARIF component-local lookup must not turn unresolved rules into clean scans."""
import copy
import unittest

from tools import sarif_rules as parser


class SarifRuleTests(unittest.TestCase):
    def fixture(self):
        return {'driver': {'name': 'CodeQL', 'guid': 'driver-guid', 'rules': [
                    {'id': 'same-id', 'guid': 'low-guid', 'properties': {'security-severity': '5.0'}}]},
                'extensions': [{'name': 'codeql/python-queries', 'guid': 'extension-guid', 'rules': [
                    {'id': 'same-id', 'guid': 'high-guid', 'properties': {'security-severity': '8.8'}},
                    {'id': 'medium', 'properties': {'security-severity': '5.0'}}]}]}

    def test_official_extension_index_is_component_local_not_driver_rule_id(self):
        tool = self.fixture()
        result = {'ruleId': 'same-id', 'rule': {'id': 'same-id', 'index': 0, 'toolComponent': {'index': 0}}}
        rule = parser.resolve_rule(tool, result)
        self.assertEqual('high-guid', rule['guid']); self.assertTrue(parser.is_blocking(rule, result))
        self.assertFalse(parser.is_blocking(parser.resolve_rule(tool, {'ruleId': 'same-id'}), {}))
        medium = {'rule': {'index': 1, 'toolComponent': {'index': 0}}}
        self.assertFalse(parser.is_blocking(parser.resolve_rule(tool, medium), medium))

    def test_guid_and_driver_references_resolve_with_redundant_identity_consistency(self):
        tool = self.fixture()
        result = {'rule': {'guid': 'HIGH-GUID', 'toolComponent': {'guid': 'EXTENSION-GUID', 'name': 'codeql/python-queries'}}}
        self.assertEqual('high-guid', parser.resolve_rule(tool, result)['guid'])
        result = {'ruleId': 'same-id', 'ruleIndex': 0, 'rule': {'id': 'same-id', 'index': 0, 'guid': 'low-guid'}}
        self.assertEqual('low-guid', parser.resolve_rule(tool, result)['guid'])
        self.assertEqual('same-id', parser.resolve_rule(tool, {'ruleId': 'same-id/subrule', 'ruleIndex': 0})['id'])

    def test_missing_ambiguous_mismatched_or_malformed_references_block(self):
        tool = self.fixture()
        bad = [
            {'ruleId': 'missing'},
            {'rule': {'index': 99, 'toolComponent': {'index': 0}}},
            {'rule': {'index': 0, 'toolComponent': {'index': 99}}},
            {'ruleId': 'same-id', 'rule': {'id': 'medium', 'index': 0, 'toolComponent': {'index': 0}}},
            {'ruleId': 'medium', 'rule': {'index': 0, 'toolComponent': {'index': 0}}},
            {'ruleIndex': 1, 'rule': {'index': 0, 'toolComponent': {'index': 0}}},
            {'ruleIndex': True, 'rule': {'index': 1, 'toolComponent': {'index': 0}}},
            {'rule': {'index': 0, 'toolComponent': {'index': True}}},
            {'rule': {'index': 0, 'toolComponent': {'index': 0, 'guid': 'driver-guid'}}},
            {'rule': {'index': 0, 'guid': 'low-guid', 'toolComponent': {'index': 0}}},
            {'rule': {'index': 0, 'toolComponent': {'name': 'codeql/python-queries'}}},
            {'rule': {'guid': 'missing', 'toolComponent': {'index': 0}}},
        ]
        for result in bad:
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                parser.resolve_rule(tool, result)
        duplicate = copy.deepcopy(tool); duplicate['driver']['rules'].append(copy.deepcopy(duplicate['driver']['rules'][0]))
        with self.assertRaises(RuntimeError): parser.resolve_rule(duplicate, {'ruleId': 'same-id'})

    def test_security_severity_invalid_unknown_and_default_error_cannot_be_downgraded(self):
        for value in ['NaN', 'Infinity', '-1', '10.1', 'unrated', None, True]:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                parser.is_blocking({'properties': {'security-severity': value}}, {})
        with self.assertRaises(RuntimeError): parser.is_blocking({'properties': {'tags': ['security']}}, {})
        self.assertTrue(parser.is_blocking({'defaultConfiguration': {'level': 'error'}}, {}))
        self.assertFalse(parser.is_blocking({'properties': {'security-severity': '6.9'}}, {}))
        self.assertTrue(parser.is_blocking({'properties': {'security-severity': '7.0'}}, {}))


if __name__ == '__main__':
    unittest.main()
