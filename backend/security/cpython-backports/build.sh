#!/bin/sh
set -eu
test "$(python -c 'import platform; print(platform.python_version())')" = 3.13.18
echo 'abb0102e6d4ae027509bcb82327d9dd85515b44a3ba8ad538cfc5e9847029f68  /tmp/Python-3.13.18.tar.xz' | sha256sum -c -
cd /patches
sha256sum -c patches.sha256
tar -xJf /tmp/Python-3.13.18.tar.xz -C /tmp
cd /tmp/Python-3.13.18
patch --fuzz=0 -p1 < /patches/CVE-2026-103995.patch
patch --fuzz=0 -p1 < /patches/CVE-2026-116566.patch
patch --fuzz=0 -p1 < /patches/CVE-2025-15367.patch
patch --fuzz=0 -p1 < /patches/CVE-2026-124249.patch
patch --fuzz=0 -p1 < /patches/CVE-2026-19552.patch
patch --fuzz=0 -p1 < /patches/CVE-2026-115559.patch
patch --fuzz=0 -p1 < /patches/CVE-2026-115791.patch
# Keep the runtime, tests and NEWS from the upstream patch. The 3.16 manual
# hunk references attributes that are absent from the 3.13 manual.
sed -n '/^diff --git a\/Lib\/http\/cookies.py /,$p' /patches/CVE-2026-16052.patch > /tmp/trip-stdlib-cookies.patch
patch --fuzz=0 -p1 < /tmp/trip-stdlib-cookies.patch
mkdir -p /out/lib-dynload /out/http /out/stdlib/zipfile /out/provenance
# Compile the patched socket extension against the official build's headers.
gcc -shared -fPIC $(python3-config --cflags) -I./Modules -I./Include/internal \
    Modules/socketmodule.c -o "/out/lib-dynload/_socket$(python3-config --extension-suffix)" \
    $(python3-config --ldflags)

# runpy is frozen in libpython and the launcher records the startup path there.
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(stat -c %Y configure.ac)
python - <<'PY'
import os
import shlex
import subprocess
import sysconfig

environment = os.environ.copy()
for name in ('CFLAGS', 'CPPFLAGS', 'LDFLAGS'):
    value = sysconfig.get_config_var('CONFIGURE_' + name)
    if value:
        environment[name] = value
arguments = shlex.split(sysconfig.get_config_var('CONFIG_ARGS'))
subprocess.run(['./configure', *arguments], env=environment, check=True)
PY
# The official images supply these flags to make, outside sysconfig.
case "$1" in
    debian)
        extra_cflags=$(dpkg-buildflags --get CFLAGS)
        ldflags=$(dpkg-buildflags --get LDFLAGS)
        ;;
    alpine)
        extra_cflags=-DTHREAD_STACK_SIZE=0x100000
        ldflags=
        ;;
    *) exit 2 ;;
esac
case "$(uname -m)" in
    x86_64|aarch64) extra_cflags="$extra_cflags -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer" ;;
    i?86) ;;
    *) extra_cflags="$extra_cflags -fno-omit-frame-pointer" ;;
esac
make -j2 "EXTRA_CFLAGS=$extra_cflags" "LDFLAGS=$ldflags -Wl,--strip-all" libpython3.13.so
snapshot_abi() {
    nm -D --defined-only "$1" > /tmp/cpython-symbols.txt
    readelf -d "$1" > /tmp/cpython-dynamic.txt
    readelf -W -l "$1" > /tmp/cpython-segments.txt
    awk '$NF ~ /^_?Py/ {print $NF}' /tmp/cpython-symbols.txt | LC_ALL=C sort -u
    awk '/\(NEEDED\)|\(SONAME\)/ {print $NF} /BIND_NOW|FLAGS_1.*NOW/ {print "BIND_NOW"}' \
        /tmp/cpython-dynamic.txt | LC_ALL=C sort -u
    awk '$1 == "GNU_RELRO" {print $1} $1 == "GNU_STACK" {printf "%s", $1; for (i=7; i<NF; i++) printf " %s", $i; print ""}' \
        /tmp/cpython-segments.txt
}
snapshot_abi /usr/local/lib/libpython3.13.so.1.0 > /tmp/cpython-original.abi
snapshot_abi libpython3.13.so.1.0 > /tmp/cpython-rebuilt.abi
diff -u /tmp/cpython-original.abi /tmp/cpython-rebuilt.abi
cp libpython3.13.so.1.0 /out/
cp /out/libpython3.13.so.1.0 /usr/local/lib/

cp Lib/http/client.py Lib/http/cookiejar.py Lib/http/cookies.py /out/http/
cp Lib/gzip.py Lib/imaplib.py Lib/poplib.py Lib/runpy.py Lib/ssl.py Lib/tarfile.py /out/stdlib/
cp Lib/zipfile/__init__.py /out/stdlib/zipfile/
cp /out/lib-dynload/_socket*.so /usr/local/lib/python3.13/lib-dynload/
cp /out/http/*.py /usr/local/lib/python3.13/http/
cp -R /out/stdlib/. /usr/local/lib/python3.13/
cp /patches/*.patch /patches/patches.sha256 /patches/README.md /out/provenance/
sha256sum /usr/local/lib/libpython3.13.so.1.0 \
    /usr/local/lib/python3.13/lib-dynload/_socket*.so \
    /usr/local/lib/python3.13/gzip.py \
    /usr/local/lib/python3.13/http/client.py \
    /usr/local/lib/python3.13/http/cookiejar.py \
    /usr/local/lib/python3.13/http/cookies.py \
    /usr/local/lib/python3.13/imaplib.py \
    /usr/local/lib/python3.13/poplib.py \
    /usr/local/lib/python3.13/runpy.py \
    /usr/local/lib/python3.13/ssl.py \
    /usr/local/lib/python3.13/tarfile.py \
    /usr/local/lib/python3.13/zipfile/__init__.py > /out/provenance/installed-files.sha256
python -c 'import _imp, runpy, sys; assert _imp.is_frozen("runpy"); assert hasattr(runpy, "_remove_sys_path0"); assert sys._path0 == ""'
