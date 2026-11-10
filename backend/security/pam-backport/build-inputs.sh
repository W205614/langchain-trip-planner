#!/bin/bash
set -euo pipefail
test "$(dpkg --print-architecture)" = amd64
test "$(dpkg-query -W -f='${Version}' libpam0g)" = 1.7.0-5
sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/debian.sources
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get build-dep -y --no-install-recommends \
    --arch-only -P nodoc pam=1.7.0-5
mkdir -p /build /out/packages /out/provenance
cd /build
apt-get source --download-only --only-source pam=1.7.0-5
sha256sum -c /pam-patches/sources.sha256
dpkg-source -x pam_1.7.0-5.dsc
