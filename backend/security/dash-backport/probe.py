"""Exercise the actual rebuilt shell without provider or business calls."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def probe(binary):
    def run(script, value):
        result = subprocess.run([str(binary), '-c', script, 'dash-security-probe', value],
                                capture_output=True, timeout=3, check=True)
        assert not result.stderr, result.stderr
        return result.stdout

    pattern = 'case "$1" in *.*.*.*.*.tar.gz) printf match;; *) printf no;; esac'
    assert run(pattern, 'a.b.c.d.e.tar.gz') == b'match'
    # Many dot positions exercised the old recursive matcher with this short
    # pattern. The selected libc implementation must remain bounded.
    assert run(pattern, '.' * 240 + 'wrong') == b'no'
    cases = []
    for suffix in ['u4e2d', 'U0001f600', 'U0010ffff', 'U7fffffff', 'u0041', 'n', '101']:
        value = chr(92) + suffix
        expected = b'\n' if suffix == 'n' else b'A' if suffix == '101' else value.encode()
        for mode, script in [('format', 'printf "$1"'), ('percent-b', 'printf "%b" "$1"')]:
            observed = run(script, value)
            assert observed == expected, (value, mode, observed.hex(), expected.hex())
            cases.append({'input_hex': value.encode().hex(), 'path': mode, 'stdout_hex': observed.hex()})
    return {'binary': str(binary), 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'bounded_pattern_cases': 2, 'printf_cases': cases,
            'boundary': 'Real shell matching and legacy printf; local synthetic arguments only.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(probe(args.binary), indent=2))
