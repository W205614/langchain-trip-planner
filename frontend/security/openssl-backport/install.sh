#!/bin/sh
set -eu
root=/tmp/trip-openssl
. "$root/provenance/source.lock"
for name in libcrypto3 libssl3; do
    apk --no-network info -e "$name=$UPSTREAM_VERSION-r1" > /dev/null
done
set -- "$root/packages/libcrypto3-$PACKAGE_VERSION.apk" "$root/packages/libssl3-$PACKAGE_VERSION.apk"
if apk --no-network info -e openssl > /dev/null; then
    apk --no-network info -e "openssl=$UPSTREAM_VERSION-r1" > /dev/null
    set -- "$@" "$root/packages/openssl-$PACKAGE_VERSION.apk"
fi
apk --keys-dir "$root/keys" verify "$@"
apk --keys-dir "$root/keys" add --no-network --no-cache "$@"
for package in "$@"; do
    name=${package##*/}
    apk --no-network info -e "${name%-$PACKAGE_VERSION.apk}=$PACKAGE_VERSION" > /dev/null
done
sha256sum -c "$root/provenance/libraries.sha256"
if apk --no-network info -e openssl > /dev/null; then
    sha256sum -c "$root/provenance/cli.sha256"
fi
mkdir -p /usr/local/share/tripplanner/openssl-backport
cp "$root"/provenance/* /usr/local/share/tripplanner/openssl-backport/
