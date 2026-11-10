#!/bin/sh
set -eu
package_dir=/tmp/trip-busybox/packages
key_dir=/tmp/trip-busybox/keys
apk --no-network info -e 'busybox=1.37.0-r31' > /dev/null
# Trust this build's package key only for this install; it is not a runtime trust anchor.
apk --keys-dir "$key_dir" add --no-network "$package_dir"/*.apk
mkdir -p /usr/local/share/tripplanner/busybox-backport
cp /tmp/trip-busybox/provenance/* /usr/local/share/tripplanner/busybox-backport/
sha256sum /bin/busybox /usr/bin/ssl_client > /usr/local/share/tripplanner/busybox-backport/runtime-sha256.txt
apk --no-network info -e 'busybox=1.37.0_p20261110-r0' > /dev/null
