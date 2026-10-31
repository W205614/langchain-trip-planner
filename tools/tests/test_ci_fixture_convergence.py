"""Keep local and CI entrypoints on the same tracked test sources."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class FixtureConvergenceTests(unittest.TestCase):
    def test_ci_executes_the_tracked_python_http_and_browser_suites_directly(self):
        workflow = (ROOT/'.github/workflows/ci.yml').read_text(encoding='utf-8')
        for retired in ('ci_contract_fixture_overlay', 'ci_time_window_benchmark', 'ci_excluded_day_contract', 'ci_browser_contract_overlay', '.ci-reviewed-playwright'):
            self.assertNotIn(retired, workflow)
        self.assertIn('python scripts/time_window_benchmark.py', workflow)
        self.assertIn('agent python scripts/final_scenarios.py', workflow)
        self.assertIn("'--config','playwright.config.ts'", workflow)
