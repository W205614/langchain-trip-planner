"""The actual CI-selected security reviews must survive calendar changes."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import unittest

from tools import codeql_context, image_policy, security_gate

ROOT = Path(__file__).resolve().parents[2]


class ActiveReviewTests(unittest.TestCase):
    def test_ci_selected_reviews_are_change_bound_and_load_in_future(self):
        workflow = (ROOT/'.github/workflows/security.yml').read_text(encoding='utf-8')
        references = set(re.findall(r'--(vex|vendor-policy|codeql-context)\s+([.\w/-]+)', workflow))
        self.assertEqual({'vex','vendor-policy','codeql-context'}, {kind for kind,_ in references})
        future = datetime(2040,1,1,tzinfo=timezone.utc)
        for kind, relative in references:
            with self.subTest(policy=relative):
                path = ROOT/relative
                if kind=='vex':
                    policy = security_gate.load_vex(path, today=future.date(), root=ROOT)
                elif kind=='vendor-policy':
                    policy = image_policy.load_policy(path, ROOT, today=future.date())
                else:
                    policy = codeql_context.load(path, ROOT, now=future)
                self.assertEqual(2,policy['schema_version'])
                self.assertTrue(policy['review_policy'])
                self.assertTrue({'expires_at','expires_on','review_expires_on'}.isdisjoint(policy))
        lock = json.loads((ROOT/'backend/runtime-security-packages.json').read_text(encoding='utf-8'))
        self.assertEqual('base_and_package_bound',lock['review_policy'])
        self.assertNotIn('review_expires_on',lock)


if __name__=='__main__':
    unittest.main()
