#!/bin/bash
set -euo pipefail
cd /pam-patches
sha256sum -c patches.sha256
cd /build/pam-1.7.0
patch --batch --fuzz=0 -p1 < /pam-patches/CVE-2026-54411.patch
python - <<'PY'
from pathlib import Path
path = Path('debian/changelog')
path.write_text('pam (1.7.0-5+trip1) trixie; urgency=high\n\n'
                '  * Backport Linux-PAM 30708d973b63891bf700299ce3ae0f1086398284\n'
                '    in full: constant-time plaintext and key_only password\n'
                '    comparisons for pam_userdb (CVE-2026-54411).\n\n'
                ' -- Trip Planner downstream build <build@tripplanner.invalid>  Tue, 10 Nov 2026 00:00:00 +0000\n\n'
                + path.read_text())
PY
export DEB_BUILD_OPTIONS='parallel=2 nodoc'
export DEB_BUILD_PROFILES=nodoc
export SOURCE_DATE_EPOCH=1794268800
# Keep Debian's original features, packaging and tests; build architecture
# packages only. The unchanged libpam-runtime configuration package stays as-is.
dpkg-buildpackage -B -uc -us -d
for package in libpam0g libpam-modules libpam-modules-bin; do
    cp /build/"${package}"_1.7.0-5+trip1_amd64.deb /out/packages/
done
cp /pam-patches/* /out/provenance/
cp debian/copyright /out/provenance/DEBIAN-COPYRIGHT
cp debian/changelog /out/provenance/DEBIAN-CHANGELOG
sha256sum /out/packages/*.deb > /out/provenance/package-sha256.txt
sha256sum debian/libpam-modules/usr/lib/x86_64-linux-gnu/security/pam_userdb.so \
    | sed 's@  debian/libpam-modules/@  /@' > /out/provenance/pam-userdb-sha256.txt
