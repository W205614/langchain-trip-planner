#!/bin/bash
set -euo pipefail
baseline_version=$(dpkg-query -W -f='${Version}' libssl3t64)
printf 'fixed runtime base libssl3t64 version: %s\n' "$baseline_version"
test "$baseline_version" = '3.5.7-1~deb13u3'
openssl version -a
test "$(dpkg-query -W -f='${Version}' libssl3t64)" = "$baseline_version"
old_crypto=$(ldconfig -p | awk '/libcrypto\.so\.3 \(libc6,x86-64\)/ { print $NF; exit }')
test -n "$old_crypto"
readelf -d "$old_crypto" | grep -F 'SONAME' | grep -F 'libcrypto.so.3'
nm -D --defined-only "$old_crypto" | awk 'NF >= 3 { print $3 }' | sort -u > /out/provenance/libcrypto-before.symbols
old_ssl=$(ldconfig -p | awk '/libssl\.so\.3 \(libc6,x86-64\)/ { print $NF; exit }')
test -n "$old_ssl"
readelf -d "$old_ssl" | grep -F 'SONAME' | grep -F 'libssl.so.3'
nm -D --defined-only "$old_ssl" | awk 'NF >= 3 { print $3 }' | sort -u > /out/provenance/libssl-before.symbols
set +e
/build/test-dgram-detect-peer > /out/provenance/dgram-peer-baseline.log 2>&1
baseline_status=$?
set -e
if [ "$baseline_status" -eq 0 ]; then
    cat /out/provenance/dgram-peer-baseline.log
    echo 'unpatched IPv4/IPv6 BIO datagram peer regression unexpectedly passed' >&2
    exit 1
fi
cat /out/provenance/dgram-peer-baseline.log
printf 'unpatched IPv4/IPv6 BIO datagram peer regression failed as expected (exit %s)\n' "$baseline_status" > /out/provenance/dgram-peer-baseline-result.txt
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --no-remove /out/packages/*.deb
test "$(dpkg-query -W -f='${Version}' libssl3t64)" = '3.5.7-1~deb13u3+trip1'
test "$(dpkg-query -W -f='${Version}' openssl)" = '3.5.7-1~deb13u3+trip1'
test "$(dpkg-query -W -f='${Version}' openssl-provider-legacy)" = '3.5.7-1~deb13u3+trip1'
new_crypto=$(ldconfig -p | awk '/libcrypto\.so\.3 \(libc6,x86-64\)/ { print $NF; exit }')
new_ssl=$(ldconfig -p | awk '/libssl\.so\.3 \(libc6,x86-64\)/ { print $NF; exit }')
test -n "$new_crypto" && test -n "$new_ssl"
readelf -d "$new_crypto" | grep -F 'SONAME' | grep -F 'libcrypto.so.3'
readelf -d "$new_ssl" | grep -F 'SONAME' | grep -F 'libssl.so.3'
nm -D --defined-only "$new_crypto" | awk 'NF >= 3 { print $3 }' | sort -u > /out/provenance/libcrypto-after.symbols
nm -D --defined-only "$new_ssl" | awk 'NF >= 3 { print $3 }' | sort -u > /out/provenance/libssl-after.symbols
cmp /out/provenance/libcrypto-before.symbols /out/provenance/libcrypto-after.symbols
cmp /out/provenance/libssl-before.symbols /out/provenance/libssl-after.symbols
/build/test-dgram-detect-peer
openssl version -a > /out/provenance/openssl-version.txt
openssl list -providers -provider default -provider legacy > /out/provenance/openssl-providers.txt
printf 'IPv4/IPv6 peer detection, package installation, SONAME and exported ABI checks passed\n' > /out/provenance/runtime-regressions.txt
