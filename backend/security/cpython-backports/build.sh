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
mkdir -p /out/lib-dynload /out/http /out/stdlib /out/provenance
# Rebuild only the changed extension against the identical release's installed
# headers/configuration; the interpreter and its ABI remain the official build.
gcc -shared -fPIC $(python3-config --cflags) -I./Modules -I./Include/internal \
    Modules/socketmodule.c -o "/out/lib-dynload/_socket$(python3-config --extension-suffix)" \
    $(python3-config --ldflags)
cp Lib/http/client.py /out/http/
cp Lib/poplib.py /out/stdlib/
cp /out/lib-dynload/_socket*.so /usr/local/lib/python3.13/lib-dynload/
cp /out/http/client.py /usr/local/lib/python3.13/http/client.py
cp /out/stdlib/poplib.py /usr/local/lib/python3.13/poplib.py
cp /patches/*.patch /patches/patches.sha256 /patches/README.md /out/provenance/
sha256sum /usr/local/lib/python3.13/lib-dynload/_socket*.so \
    /usr/local/lib/python3.13/http/client.py \
    /usr/local/lib/python3.13/poplib.py > /out/provenance/installed-files.sha256
# The upstream tests use local sockets; network resource tests stay disabled.
PYTHONPATH=/tmp/Python-3.13.18/Lib python -m test -j2 test_socket test_httplib test_poplib
