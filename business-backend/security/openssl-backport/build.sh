#!/bin/sh
set -eu
. /trip-openssl/source.lock
test "$(apk --print-arch)" = x86_64
export SOURCE_DATE_EPOCH SRCDEST=/sources
export JOBS=4 HARNESS_JOBS=4
export PACKAGER='Trip Planner downstream build <build@tripplanner.invalid>'
mkdir -p /aports/openssl /out/packages /out/keys /out/provenance
cd /aports/openssl
echo "$RECIPE_SHA256  /trip-openssl/recipe.tar.gz" | sha256sum -c -
tar -xzf /trip-openssl/recipe.tar.gz

archive="$SRCDEST/openssl-$UPSTREAM_VERSION.tar.gz"
checksum="$UPSTREAM_SHA256  $archive"
if ! echo "$checksum" | sha256sum -cs -; then
    url="https://deb.debian.org/debian/pool/main/o/openssl/openssl_$UPSTREAM_VERSION.orig.tar.gz"
    for attempt in 1 2 3; do
        wget -c -T 60 -O "$archive" "$url" && break
        test "$attempt" -lt 3 || exit 1
    done
fi
echo "$checksum" | sha256sum -c -
abuild -F verify
abuild-keygen -a -n
cp /root/.abuild/*.pub /etc/apk/keys/
abuild -F > /tmp/openssl-build.log 2>&1 || {
    tail -n 80 /tmp/openssl-build.log >&2
    exit 1
}
grep -E '^Files=|^Result:' /tmp/openssl-build.log
for name in libcrypto3 libssl3 openssl; do
    cp "/root/packages/aports/x86_64/$name-$PACKAGE_VERSION.apk" /out/packages/
done
cp /root/.abuild/*.pub /out/keys/
cp /trip-openssl/source.lock /trip-openssl/recipe.tar.gz /trip-openssl/*.sha256 /out/provenance/
cp /trip-openssl/install.sh /out/
