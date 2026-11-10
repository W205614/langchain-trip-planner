#!/bin/bash
set -euo pipefail
test "$(dpkg --print-architecture)" = amd64
test "$(dpkg-query -W -f='${Version}' dash)" = 0.5.12-12
sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/debian.sources
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get build-dep -y --no-install-recommends --arch-only dash=0.5.12-12
mkdir -p /build /out/packages /out/provenance
cd /build
apt-get source --download-only --only-source dash=0.5.12-12
sha256sum -c /dash-patches/sources.sha256
dpkg-source -x dash_0.5.12-12.dsc
