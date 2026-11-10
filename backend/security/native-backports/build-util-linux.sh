#!/bin/bash
set -euo pipefail
cd /patches
sha256sum -c util-linux-patches.sha256
cd /build/util-linux-2.41.5
while read -r patch_file; do
  patch --batch --fuzz=0 -p1 < "/patches/$patch_file"
done < /patches/util-linux-patches.txt
python - <<'PY'
from pathlib import Path
path = Path('debian/changelog')
path.write_text('util-linux (2.41.5-0+deb13u1+trip2) trixie; urgency=high\n\n'
                '  * Backport official stable/v2.41 security fixes and follow-ups.\n\n'
                '  * Preserve the original login -h FQDN for PAM_RHOST (CVE-2026-3184).\n\n'
                ' -- Trip Planner <security@localhost>  Tue, 10 Nov 2026 00:00:00 +0000\n\n'
                + path.read_text())
PY
export DEB_BUILD_OPTIONS='parallel=2 nocheck nodoc'
export DEB_BUILD_PROFILES='nocheck nodoc'
export SOURCE_DATE_EPOCH=1794268800
dpkg-buildpackage -B -uc -us -d
gcc -D_GNU_SOURCE -DHAVE_CONFIG_H -include config.h -I. -Iinclude -Ilibmount/src \
  /patches/openat2-regression.c -Wl,--start-group .libs/libmount.a .libs/libcommon.a .libs/libblkid.a \
  -Wl,--end-group -lselinux -ldl -o /build/openat2-regression
/build/openat2-regression
gcc -shared -fPIC -Wall -Wextra /patches/login-pam-rhost-regression.c \
  -o /build/login-pam-rhost-regression.so
python /patches/check-login-pam-rhost.py
# Preserve the runtime package set. Development tools remain in this build stage.
for package in bsdutils libblkid1 liblastlog2-2 libmount1 libsmartcols1 libuuid1 login mount util-linux; do
  cp /build/"${package}"_*trip2_amd64.deb /out/
done
