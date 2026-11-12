#!/bin/bash
set -euo pipefail
test "$(dpkg --print-architecture)" = amd64
sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/debian.sources
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get build-dep -y --no-install-recommends --arch-only -P nodoc openssl=3.5.7-1~deb13u3
mkdir -p /build /out/packages /out/provenance
cd /build
apt-get source --download-only --only-source openssl=3.5.7-1~deb13u3
sha256sum -c /openssl-patches/sources.sha256
dpkg-source -x openssl_3.5.7-1~deb13u3.dsc
