"""A native-library security patch must never turn into a core distro upgrade."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('runtime_patch', Path(__file__).resolve().parents[2] / 'backend/scripts/install_runtime_security_patches.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


class RuntimePackageLockTests(unittest.TestCase):
    def test_exact_two_upgrade_plan_is_accepted_but_new_removed_or_core_packages_fail(self):
        valid = '2 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\nInst libacl1 [old] (new)\nInst zlib1g [old] (new)\n'
        patch.validate_simulation(valid, {'libacl1', 'zlib1g'})
        invalid = [valid + 'Inst libc6 [old] (new)\n', valid + 'Remv essential [old]\n',
                   valid.replace('0 newly installed', '1 newly installed'),
                   valid.replace('Inst zlib1g', 'Inst libstdc++6')]
        for scenario in invalid:
            with self.assertRaises(RuntimeError): patch.validate_simulation(scenario, {'libacl1', 'zlib1g'})


if __name__ == '__main__':
    unittest.main()
