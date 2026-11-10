#!/bin/sh
# Recheck the installed runtime, without the SDK, compiler or a writable image.
set -eu
ulimit -c 0
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$here"
sha256sum -c SHA256SUMS
sha256sum -c packaged-elf-sha256.txt
python3 - "$here" <<'PY'
import json, os, pathlib, signal, subprocess, sys, tempfile
here = pathlib.Path(sys.argv[1])
logs = pathlib.Path(tempfile.mkdtemp(prefix='glibc-hardening-', dir='/tmp'))
cases = [
    ('tls-guard-regression', [], ''),
    ('stack-randomization-regression', ['verify'], ''),
    ('stack-randomization-regression', ['entropy-denied'], ''),
    ('stack-randomization-regression', ['verify'], 'glibc.pthread.stack_cache_size=41943040'),
    ('stack-independent-lifecycle', [], ''),
    *[('arena-regression', [], f'glibc.malloc.hugetlb={n}') for n in range(3)],
    ('arena-regression', ['--block-entropy'], ''),
]
results = []
for index, (name, args, tunables) in enumerate(cases):
    env = {k: v for k, v in os.environ.items() if k not in ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'LD_AUDIT')}
    env['GLIBC_TUNABLES'] = tunables
    # No explicit loader or library path: PT_INTERP and installed system libc
    # are the objects under test, after the package ELF hashes above match.
    result = subprocess.run([str(here / name), *args], env=env,
                            capture_output=True, text=True, timeout=120)
    (logs / f'{index:02d}.out').write_text(result.stdout)
    (logs / f'{index:02d}.err').write_text(result.stderr)
    fatal = args == ['--block-entropy']
    valid = ((result.returncode == -signal.SIGABRT and result.stdout == ''
              and result.stderr == 'malloc: cannot initialize arena randomization\n')
             if fatal else result.returncode == 0 and result.stderr == '')
    entry = dict(binary=name, args=args, tunables=tunables,
                 status=result.returncode, passed=valid)
    results.append(entry)
    if not valid:
        print(json.dumps(dict(result='failed', case=entry, logs=str(logs))), flush=True)
        print(result.stdout, end='')
        print(result.stderr, end='', file=sys.stderr)
        raise SystemExit(1)
print(json.dumps(dict(result='passed', cases=results, logs=str(logs),
                      boundary='Linux x86_64 LP64; detached stacks may be reclaimed on a subsequent create; fork probe is Linux/glibc-specific')), flush=True)
PY
