"""Real checkout for tests of the frozen October 27 two-file authorization."""
from pathlib import Path
import subprocess
import tempfile


def checkout(test_case, repository):
    fixture = tempfile.TemporaryDirectory(prefix='trip-legacy-review-')
    test_case.addClassCleanup(fixture.cleanup)
    root = Path(fixture.name) / 'repository'
    subprocess.run(['git', '-c', 'core.autocrlf=false', 'clone', '--shared', '--quiet', '--no-checkout',
                    str(repository), str(root)], check=True)
    subprocess.run(['git', '-c', 'core.autocrlf=false', 'checkout', '--quiet', '--detach',
                    '513b5727f522453c8fc10e9cbd55bcbdabb717c2'], cwd=root, check=True)
    return root
