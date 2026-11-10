#!/bin/bash
set -euo pipefail
package_revision=trip3
case "${1:-}" in
  '') test "$#" -eq 0 ;;
  --hardening) test "$#" -eq 1; package_revision=trip4 ;;
  *) printf 'Usage: build-glibc.sh [--hardening]\n' >&2; exit 2 ;;
esac
cd /patches
sha256sum -c glibc-build.sha256
cd /build/glibc-2.41
while read -r patch_file; do
  patch --batch --fuzz=0 -p1 < "/patches/$patch_file"
done < /patches/glibc-patches.txt
if [ "$package_revision" = trip4 ]; then
  (cd /hardening && sha256sum -c hardening-patches.sha256)
  for patch_file in tls-guard.patch stack-randomization.patch arena-randomization.patch; do
    patch --batch --fuzz=0 -p1 < "/hardening/$patch_file"
  done
fi
python - "$package_revision" <<'PY'
from pathlib import Path
import sys
path = Path('debian/changelog')
hardening = ('  * Add reviewed downstream x86-64 TLS guards, independently randomized\n'
             '    thread stacks without cache reuse, and randomized thread arenas.\n\n'
             if sys.argv[1] == 'trip4' else '')
path.write_text(f'glibc (2.41-12+deb13u4+{sys.argv[1]}) trixie; urgency=high\n\n'
                '  * Retain CVE-2026-19499 and CVE-2026-5435 fixes.\n'
                '  * Backport reviewed upstream LANGUAGE traversal patch for CVE-2026-84243.\n\n'
                '  * Backport bounded DNS printing, wordexp, fopen, tdelete, iconv,\n'
                '    resolver search-list and trusted ORIGIN path fixes.\n\n'
                + hardening
                + ' -- Trip Planner downstream build <build@tripplanner.invalid>  Tue, 10 Nov 2026 00:00:00 +0000\n\n'
                + path.read_text())
PY
export DEB_BUILD_OPTIONS='parallel=2 nocheck nobench'
export DEB_BUILD_PROFILES='nobiarch nocheck noudeb'
export SOURCE_DATE_EPOCH=1794268800
# Only runtime packages are installed; generating every locale is unnecessary.
make -f debian/rules build_libc
make -f debian/rules /build/glibc-2.41/stamp-dir/build_C.utf8
make -f debian/rules binaryinst_libc6 binaryinst_libc-bin
for library in libc.so.6 libresolv.so.2 ld-linux-x86-64.so.2; do
  nm -D --defined-only "/usr/lib/x86_64-linux-gnu/$library" | awk '{print $NF}' | sort -u > /build/glibc-before.symbols
  nm -D --defined-only "debian/libc6/usr/lib/x86_64-linux-gnu/$library" | awk '{print $NF}' | sort -u > /build/glibc-after.symbols
  comm -23 /build/glibc-before.symbols /build/glibc-after.symbols > /build/glibc-missing.symbols
  test ! -s /build/glibc-missing.symbols
done
cp "/build/libc6_2.41-12+deb13u4+${package_revision}_amd64.deb" /out/
cp "/build/libc-bin_2.41-12+deb13u4+${package_revision}_amd64.deb" /out/
