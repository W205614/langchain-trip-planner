#!/bin/bash
set -euo pipefail
cd /build/glibc-2.41
patch --batch --fuzz=0 -p1 < /patches/patches/glibc-19499-sourceware.patch
patch --batch --fuzz=0 -p1 < /patches/patches/glibc-5435-trixie.patch
python - <<'PY'
from pathlib import Path
path = Path('debian/changelog')
path.write_text('glibc (2.41-12+deb13u4+trip1) trixie; urgency=high\n\n'
                '  * Backport upstream CVE-2026-19499 and CVE-2026-5435 fixes.\n\n'
                ' -- Trip Planner <security@localhost>  Fri, 30 Oct 2026 00:00:00 +0000\n\n'
                + path.read_text())
PY
export DEB_BUILD_OPTIONS='parallel=2 nocheck nobench'
export DEB_BUILD_PROFILES='nobiarch nocheck noudeb'
export SOURCE_DATE_EPOCH=1793318400
# Only runtime packages are installed; generating every locale is unnecessary.
make -f debian/rules build_libc
make -f debian/rules /build/glibc-2.41/stamp-dir/build_C.utf8
make -f debian/rules binaryinst_libc6 binaryinst_libc-bin
make -C build-tree/amd64-libc -j2 check subdirs=stdlib tests=tst-strfmon-bug34510
for library in libc.so.6 libresolv.so.2 ld-linux-x86-64.so.2; do
  nm -D --defined-only "/usr/lib/x86_64-linux-gnu/$library" | awk '{print $NF}' | sort -u > /build/glibc-before.symbols
  nm -D --defined-only "debian/libc6/usr/lib/x86_64-linux-gnu/$library" | awk '{print $NF}' | sort -u > /build/glibc-after.symbols
  comm -23 /build/glibc-before.symbols /build/glibc-after.symbols > /build/glibc-missing.symbols
  test ! -s /build/glibc-missing.symbols
done
cp /build/libc6_2.41-12+deb13u4+trip1_amd64.deb /out/
cp /build/libc-bin_2.41-12+deb13u4+trip1_amd64.deb /out/
