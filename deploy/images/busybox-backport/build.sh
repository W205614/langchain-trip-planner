#!/bin/sh
set -eu
. /trip-busybox/source.lock
export SOURCE_DATE_EPOCH
mkdir -p /work/main/busybox /var/cache/distfiles /out/packages /out/keys /out/provenance
# The observed source transfer closed at 96%; retain its partial bytes and
# bound resumption. The mandatory original checksums below still gate use.
fetch_source() {
    for attempt in 1 2 3; do
        wget -c -T 60 -O "$2" "$1" && return 0
        test "$attempt" -lt 3 || return 1
    done
}
echo "$APKBUILD_SHA256  /trip-busybox/aports-busybox.tar.gz" | sha256sum -c -
tar -xzf /trip-busybox/aports-busybox.tar.gz --strip-components=3 -C /work/main/busybox
fetch_source "https://busybox.net/downloads/busybox-$UPSTREAM_VERSION.tar.bz2" "/var/cache/distfiles/busybox-$UPSTREAM_VERSION.tar.bz2"
echo "$UPSTREAM_SHA256  /var/cache/distfiles/busybox-$UPSTREAM_VERSION.tar.bz2" | sha256sum -c -
cd /work/main/busybox
grep -qx 'pkgver=1.37.0' APKBUILD
grep -qx 'pkgrel=31' APKBUILD
# Verify every original Alpine input before making the downstream recipe.
abuild -F verify
cp /trip-busybox/CVE-2025-60876.patch .
sed -i \
    -e "s/^pkgver=1.37.0$/_upstream=1.37.0\npkgver=$PACKAGE_VERSION/" \
    -e 's/^pkgrel=31$/pkgrel=0/' \
    -e 's@busybox-$pkgver.tar.bz2@busybox-$_upstream.tar.bz2@' \
    -e 's/^# Maintainer:/# Upstream maintainer:/' \
    -e '/^prepare() {$/a\
\tlocal wget_test\
\tfor wget_test in wget--O-overrides--P wget-handles-empty-path wget-retrieves-google-index wget-supports--P; do\
\t\tsed -i "s@http://www.google.com@http://127.0.0.1:18809@g" "$builddir/testsuite/wget/$wget_test" || return 1\
\tdone' \
    APKBUILD
cat >> APKBUILD <<'RECIPE'

# Trip Planner downstream rebuild; original Alpine sources and tests retained.
maintainer="Trip Planner downstream build <build@tripplanner.invalid>"
source="$source CVE-2025-60876.patch"
builddir="$srcdir/busybox-$_upstream"
RECIPE
export PACKAGER='Trip Planner downstream build <build@tripplanner.invalid>'
abuild-keygen -a -n
cp /root/.abuild/*.pub /etc/apk/keys/
abuild -F checksum
# Keep the four real wget tests while removing their public-site dependency.
apk add --no-cache busybox-extras
mkdir -p /tmp/wget-fixture
printf '%s\n' 'BusyBox wget test fixture' > /tmp/wget-fixture/index.html
busybox-extras httpd -f -p 127.0.0.1:18809 -h /tmp/wget-fixture &
fixture_pid=$!
trap 'kill "$fixture_pid" 2>/dev/null || true' EXIT
for attempt in 1 2 3 4 5; do
    if [ "$(wget -T 2 -qO- http://127.0.0.1:18809/)" = 'BusyBox wget test fixture' ]; then
        break
    fi
    test "$attempt" -lt 5 || exit 1
    sleep 1
done
abuild -F
kill "$fixture_pid"
wait "$fixture_pid" || true
trap - EXIT
for name in busybox busybox-binsh ssl_client; do
    package="/root/packages/$(basename "$(dirname "$PWD")")/$(apk --print-arch)/$name-$PACKAGE_VERSION-r0.apk"
    test -f "$package"
    cp "$package" /out/packages/
done
cp /root/.abuild/*.pub /out/keys/
cp APKBUILD /trip-busybox/source.lock /trip-busybox/CVE-2025-60876.patch \
    /trip-busybox/aports-busybox.tar.gz /out/provenance/
sha256sum /out/packages/*.apk > /out/provenance/package-sha256.txt
cp /trip-busybox/install.sh /out/install.sh
