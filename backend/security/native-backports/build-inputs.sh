#!/bin/bash
set -euo pipefail
mode=${1:---all}
source_cache=${2:-/build}
case "$mode" in
  --all|--dependencies-only|--sources-only) ;;
  *) printf 'Usage: build-inputs.sh [--all|--dependencies-only|--sources-only] [source-cache]\n' >&2; exit 2 ;;
esac
test "$(dpkg --print-architecture)" = amd64
test "$(dpkg-query -W -f='${Version}' libc6)" = '2.41-12+deb13u4'
if [ "$mode" != --sources-only ]; then
sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/debian.sources
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get build-dep -y --no-install-recommends \
  -P nobiarch,nocheck,nodoc glibc util-linux
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  gcc-14 g++-14 libgmp-dev libmpfr-dev libmpc-dev libisl-dev flex texinfo \
  autoconf automake libtool pkg-config gettext libcap-ng-dev libpam0g-dev \
  libselinux1-dev libncurses-dev libreadline-dev zlib1g-dev libsqlite3-dev
test "$(dpkg-query -W -f='${Version}' g++-14)" = '14.2.0-19'
fi
if [ "$mode" = --dependencies-only ]; then
  exit 0
fi
mkdir -p /build /out
mkdir -p "$source_cache"
# apt's unprivileged downloader must own its private source-cache directory.
chown _apt:root "$source_cache"
chmod 0700 "$source_cache"
cd "$source_cache"
apt-get source --download-only --only-source \
  glibc=2.41-12+deb13u4 gcc-14=14.2.0-19 util-linux=2.41.5-0+deb13u1
sha256sum -c /patches/sources.sha256
if [ "$source_cache" != /build ]; then
  while read -r digest name; do
    install -m 0644 "$source_cache/$name" "/build/$name"
  done < /patches/sources.sha256
fi
cd /build
dpkg-source -x glibc_2.41-12+deb13u4.dsc
dpkg-source -x gcc-14_14.2.0-19.dsc
dpkg-source -x util-linux_2.41.5-0+deb13u1.dsc
cd /build/gcc-14-14.2.0
make -f debian/rules patch
