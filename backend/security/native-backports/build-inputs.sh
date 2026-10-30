#!/bin/bash
set -euo pipefail
cd /patches
sha256sum -c patches.sha256
test "$(dpkg --print-architecture)" = amd64
test "$(dpkg-query -W -f='${Version}' libc6)" = '2.41-12+deb13u4'
sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/debian.sources
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get build-dep -y --no-install-recommends \
  -P nobiarch,nocheck,nodoc glibc util-linux
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  gcc-14 g++-14 libgmp-dev libmpfr-dev libmpc-dev libisl-dev flex texinfo \
  autoconf automake libtool pkg-config gettext libcap-ng-dev libpam0g-dev \
  libselinux1-dev libncurses-dev libreadline-dev zlib1g-dev libsqlite3-dev
test "$(dpkg-query -W -f='${Version}' g++-14)" = '14.2.0-19'
mkdir -p /build /out
cd /build
apt-get source --download-only --only-source \
  glibc=2.41-12+deb13u4 gcc-14=14.2.0-19 util-linux=2.41.5-0+deb13u1
sha256sum -c /patches/sources.sha256
dpkg-source -x glibc_2.41-12+deb13u4.dsc
dpkg-source -x gcc-14_14.2.0-19.dsc
dpkg-source -x util-linux_2.41.5-0+deb13u1.dsc
cd /build/gcc-14-14.2.0
make -f debian/rules patch
