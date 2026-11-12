#!/bin/bash
set -euo pipefail
cd /openssl-patches
sha256sum -c patches.sha256
cd /build/openssl-3.5.7
for patch in /openssl-patches/patches/*.patch; do
  case "$(basename "$patch")" in
    0006-CVE-2026-88823-test.patch) continue ;;
  esac
  patch --batch --fuzz=0 -p1 < "$patch"
done
python /openssl-patches/adapt_aad_regression.py test/evp_extra_test.c
python - <<'PY'
from pathlib import Path
path = Path('debian/changelog')
path.write_text('openssl (3.5.7-1~deb13u3+trip1) trixie-security; urgency=high\n\n'
                '  * Backport OpenSSL upstream fixes for CVE-2026-107496, CVE-2026-88822,\n'
                '    CVE-2026-88823, CVE-2026-95691 and CVE-2026-95692, retaining\n'
                '    Debian trixie packaging and its existing patch series.\n\n'
                ' -- Trip Planner Security <security@tripplanner.invalid>  Wed, 11 Nov 2026 00:00:00 +0000\n\n'
                + path.read_text())
PY
export DEB_BUILD_OPTIONS='parallel=2 nodoc'
export SOURCE_DATE_EPOCH=1794268800
# Keep the complete Debian source build and tests; only documentation is omitted.
dpkg-buildpackage -b -uc -us -d
for package in libssl3t64 openssl openssl-provider-legacy; do
  file="/build/${package}_3.5.7-1~deb13u3+trip1_amd64.deb"
  test -s "$file"
  cp "$file" /out/packages/
done
cp /openssl-patches/sources.sha256 /out/provenance/
cp /openssl-patches/patches.sha256 /out/provenance/
cp /openssl-patches/patches/*.patch /out/provenance/
cp debian/copyright /out/provenance/DEBIAN-COPYRIGHT
cp debian/changelog /out/provenance/DEBIAN-CHANGELOG
cp debian/patches/series /out/provenance/DEBIAN-PATCH-SERIES
sha256sum /out/packages/*.deb > /out/provenance/package-sha256.txt
printf 'Debian OpenSSL package build completed with retained Debian patch series\n'
