#!/bin/bash
# Run only after the patched libc and its Debian packages have been built.
set -euo pipefail
export LC_ALL=C
ulimit -c 0
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source=${1:-/build/glibc-2.41}
output=${2:-/build/hardening-checks}
build="$source/build-tree/amd64-libc"
mkdir -p "$output/bin" "$output/logs" /hardening-proof
cd "$here"
sha256sum -c tests.sha256
sha256sum -c hardening-patches.sha256
cmp stack-randomization.h "$source/nptl/stack-randomization.h"
for name in tls-guard-regression stack-randomization-regression arena-regression stack-independent-lifecycle stack-independent-helper; do
  gcc -std=gnu11 -O2 -D_FORTIFY_SOURCE=2 -UNDEBUG -Wall -Wextra -Werror -pthread \
    -I "$here/test-include" "$here/$name.c" -o "$output/bin/$name"
  readelf -l "$output/bin/$name" | grep -F '[Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]'
  if readelf -d "$output/bin/$name" | grep -E '\((RPATH|RUNPATH)\)'; then
    echo "Unexpected build-path linkage: $name" >&2; exit 1
  fi
done
python3 - "$here" "$output" <<'PY'
import hashlib, json, os, pathlib, signal, subprocess, sys, tarfile
here, output = map(pathlib.Path, sys.argv[1:])
packages = list(pathlib.Path('/out').glob('libc6_*_amd64.deb'))
assert len(packages) == 1, packages
package = packages[0]
libdir = output / 'package-libc'
libdir.mkdir(exist_ok=True)
names = {'libc.so.6', 'ld-linux-x86-64.so.2', 'libresolv.so.2'}
found = set()
process = subprocess.Popen(['dpkg-deb', '--fsys-tarfile', str(package)], stdout=subprocess.PIPE)
with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
    for member in archive:
        if member.name.removeprefix('./') in {'usr/lib/x86_64-linux-gnu/' + n for n in names}:
            assert member.isfile(), member.name
            name = pathlib.PurePosixPath(member.name).name
            (libdir / name).write_bytes(archive.extractfile(member).read())
            (libdir / name).chmod(member.mode)
            found.add(name)
assert process.wait() == 0
assert found == names, found
elfs = {n: hashlib.sha256((libdir / n).read_bytes()).hexdigest() for n in sorted(names)}
(output / 'packaged-elf-sha256.txt').write_text(''.join(f'{h}  /usr/lib/x86_64-linux-gnu/{n}\n' for n, h in elfs.items()))
loader = str(libdir / 'ld-linux-x86-64.so.2')
results = []
def run(name, args=(), tunables='', fatal=False):
    env = {k: v for k, v in os.environ.items() if k not in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'LD_AUDIT')}
    env['GLIBC_TUNABLES'] = tunables
    command = [loader, '--library-path', str(libdir), str(output / 'bin' / name), *args]
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
    index = len(results)
    (output / 'logs' / f'independent-{index:02d}.out').write_text(completed.stdout)
    (output / 'logs' / f'independent-{index:02d}.err').write_text(completed.stderr)
    entry = dict(binary=name, args=list(args), tunables=tunables, status=completed.returncode)
    results.append(entry)
    if fatal:
        assert completed.returncode == -signal.SIGABRT, entry
        assert completed.stdout == '', completed.stdout
        assert completed.stderr == 'malloc: cannot initialize arena randomization\n', completed.stderr
    else:
        assert completed.returncode == 0, (entry, completed.stdout, completed.stderr)
        assert not completed.stderr, completed.stderr
    return completed.stdout
run('tls-guard-regression')
run('stack-randomization-regression', ['verify'])
run('stack-randomization-regression', ['entropy-denied'])
run('stack-randomization-regression', ['verify'], 'glibc.pthread.stack_cache_size=41943040')
run('stack-independent-lifecycle')
run('stack-independent-helper')
meminfo = {line.split(':')[0]: line.split(':')[1].strip() for line in pathlib.Path('/proc/meminfo').read_text().splitlines() if line.startswith(('HugePages_', 'Hugepagesize:'))}
huge = int(meminfo['Hugepagesize'].split()[0]) * 1024
batch = []
for mode in range(3):
    modulus = huge * 4 if mode == 2 else 64 * 1024 * 1024
    residues = []
    for _ in range(8):
        data = json.loads(run('arena-regression', tunables=f'glibc.malloc.hugetlb={mode}'))
        starts = data['heap_mapping_starts']
        assert len(starts) == 4
        observed = {start % modulus for start in starts}
        assert len(observed) == 1, data
        residues.append(observed.pop())
    assert len(set(residues)) > 1, (mode, residues)
    batch.append(dict(hugetlb=mode, modulus=modulus, samples=8, residues=residues))
run('arena-regression', ['--block-entropy'], fatal=True)
summary = dict(independent=results, arena_batch=batch, hugepages=meminfo,
               packaged_libc=package.name, packaged_elf_sha256=elfs,
               limits=['Linux x86_64 LP64 only', 'Finite address samples do not prove CSPRNG quality',
                       'Physical hugepage success is not established when HugePages_Total is zero',
                       'Stack cache intentionally disabled; TLS test does not require reuse',
                       'SDK gate is followed by final installed runtime checks'])
(output / 'independent-results.json').write_text(json.dumps(summary, indent=2) + '\n')
print('Independent packaged-libc checks: PASS; arena fresh execs: 24; exact entropy abort: PASS', flush=True)
PY
# glibc's single-test target deletes the .out before executing; it does not
# permit a previous test-result to stand in for the current run.
while read -r test; do
  case "$test" in nptl/*) ;; *) continue ;; esac
  name=${test#nptl/}
  echo "Running upstream $test"
  if ! make -C "$build" -j2 test "t=$test" "tests=$name" tests-container= tests-special= >"$output/logs/${name}.log" 2>&1; then
    tail -100 "$output/logs/${name}.log"; exit 1
  fi
  grep -Fx "PASS: $test" "$build/$test.test-result"
done < upstream-tests.txt
# Delete only named generated test outputs in the private libc build tree.
# This makes repeat invocations execute the 45 checks again rather than read
# successful results left over from an earlier gate.
python3 - "$build" "$here/upstream-tests.txt" <<'PY'
import pathlib, sys
build, manifest = map(pathlib.Path, sys.argv[1:])
for test in manifest.read_text().splitlines():
    if test.startswith('nptl/'):
        continue
    for suffix in ('.out', '.test-result'):
        (build / (test + suffix)).unlink(missing_ok=True)
PY
if ! make -C "$build" -j2 subdirs=malloc \
  tests='tst-aligned-alloc-random-thread-cross tst-malloc-thread-exit tst-mallocfork2 tst-mallocfork3 tst-trim1 tst-realloc tst-malloc_info tst-malloc-random' \
  check >"$output/logs/malloc-upstream.log" 2>&1; then
  tail -120 "$output/logs/malloc-upstream.log"
  if test -f "$build/lint-makefiles.out"; then cat "$build/lint-makefiles.out"; fi
  exit 1
fi
python3 - "$here" "$build" "$output" <<'PY'
import hashlib, json, pathlib, shutil, sys
here, build, output = map(pathlib.Path, sys.argv[1:])
tests = here.joinpath('upstream-tests.txt').read_text().splitlines()
assert len(tests) == 56 and len(set(tests)) == 56
actual = []
for test in tests:
    result = (build / (test + '.test-result')).read_text().splitlines()
    assert result in (['PASS: ' + test], ['PASS: ' + test, 'original exit status 0']), (test, result)
    actual.append(result[0])
    target = output / 'upstream-results' / (test + '.test-result')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(build / (test + '.test-result'), target)
    log = build / (test + '.out')
    if log.exists():
        shutil.copyfile(log, target.with_suffix('.out'))
summary = json.loads((output / 'independent-results.json').read_text())
summary.update(upstream_nptl_pass=11, upstream_malloc_pass=45, upstream_results=actual,
               result='passed', proof_binary_interpreter='/lib64/ld-linux-x86-64.so.2')
(output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
proof = pathlib.Path('/hardening-proof')
for name in ('tls-guard-regression', 'stack-randomization-regression', 'arena-regression', 'stack-independent-lifecycle'):
    shutil.copyfile(output / 'bin' / name, proof / name)
    (proof / name).chmod(0o755)
for name in ('summary.json', 'packaged-elf-sha256.txt'):
    shutil.copyfile(output / name, proof / name)
shutil.copyfile(here / 'runtime-check.sh', proof / 'runtime-check.sh')
inputs = sorted({here / line.split('  ', 1)[1] for manifest in ('tests.sha256', 'hardening-patches.sha256') for line in (here / manifest).read_text().splitlines()} | {here / 'tests.sha256', here / 'hardening-patches.sha256'})
(proof / 'SOURCE-INPUTS.sha256').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(here)}\n' for p in inputs))
(proof / 'RUN-CHECKS.txt').write_text('''Run sh runtime-check.sh in the final installed Agent image (no compiler required).
This verifies all hashes and runs the following cases with bounded logs in /tmp:
sha256sum -c SHA256SUMS
sha256sum -c packaged-elf-sha256.txt
./tls-guard-regression
./stack-randomization-regression verify
./stack-randomization-regression entropy-denied
GLIBC_TUNABLES=glibc.pthread.stack_cache_size=41943040 ./stack-randomization-regression verify
./stack-independent-lifecycle
GLIBC_TUNABLES=glibc.malloc.hugetlb=0 ./arena-regression
GLIBC_TUNABLES=glibc.malloc.hugetlb=1 ./arena-regression
GLIBC_TUNABLES=glibc.malloc.hugetlb=2 ./arena-regression
Use a subprocess with core dumps disabled for ./arena-regression --block-entropy:
require SIGABRT (-6 in Python), empty stdout and exactly
malloc: cannot initialize arena randomization\\n
on stderr. An arbitrary crash is not a passing entropy test.
SOURCE-INPUTS.sha256 records canonical source hashes; sources ship in source materials.
The helper syscall-boundary harness runs in the SDK only; it is not a runtime binary.
''')
(proof / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(proof.iterdir()) if p.name != 'SHA256SUMS'))
print('Hardening gate: PASS; 11 NPTL + 45 malloc strict PASS; proof directory exported', flush=True)
PY
