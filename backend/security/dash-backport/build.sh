#!/bin/bash
set -euo pipefail
cd /dash-patches
sha256sum -c patches.sha256
cd /build/dash-0.5.12
patch --batch --fuzz=0 -p1 < /dash-patches/enable-libc-fnmatch.patch
python - <<'PY'
from pathlib import Path
path = Path('debian/changelog')
path.write_text('dash (0.5.12-12+trip1) trixie; urgency=high\n\n'
                '  * Use the existing upstream libc fnmatch build option to avoid\n'
                '    the recursive internal pattern matcher (CVE-2026-102473).\n'
                '    Retain all Debian patches and other feature choices.\n\n'
                ' -- Trip Planner downstream build <build@tripplanner.invalid>  Tue, 10 Nov 2026 00:00:00 +0000\n\n'
                + path.read_text())
PY
export DEB_BUILD_OPTIONS=parallel=2
export SOURCE_DATE_EPOCH=1794268800
dpkg-buildpackage -B -uc -us -d
built=$(python - <<'PY'
from pathlib import Path
paths = [p for p in (Path('debian/dash/bin/dash'), Path('debian/dash/usr/bin/dash')) if p.is_file()]
assert len(paths) == 1, paths
print(paths[0])
PY
)
grep -qx '#define HAVE_FNMATCH 1' config.h
nm -D "$built" > /out/provenance/dash.dynamic-symbols.txt
grep -Eq ' U fnmatch@' /out/provenance/dash.dynamic-symbols.txt
python /dash-patches/probe.py --binary "$PWD/$built" > /out/provenance/behavior-probe.json
cp /build/dash_0.5.12-12+trip1_amd64.deb /out/packages/
cp /dash-patches/* /out/provenance/
cp config.h config.log debian/rules debian/patches/series /out/provenance/
cp debian/copyright /out/provenance/DEBIAN-COPYRIGHT
cp debian/changelog /out/provenance/DEBIAN-CHANGELOG
sha256sum "$built" | sed "s@  $built@  /bin/dash@" > /out/provenance/dash-sha256.txt
sha256sum /out/packages/*.deb > /out/provenance/package-sha256.txt
