#!/bin/bash
set -euo pipefail
cd /patches
sha256sum -c libstdcxx-patches.sha256
cd /build/gcc-14-14.2.0/src
patch --batch --fuzz=0 -p1 < /patches/patches/gcc-95619.patch
patch --batch --fuzz=0 -p1 < /patches/patches/gcc-102010.patch
patch --batch --fuzz=0 -p1 < /patches/patches/gcc-115456.patch
mkdir -p /build/libstdcxx-build /build/libgcc
ln -s /build/gcc-14-14.2.0/src/libgcc/gthr-posix.h /build/libgcc/gthr-default.h
cd /build/libstdcxx-build
export SOURCE_DATE_EPOCH=1794268800
CXXFLAGS='-O2 -g -ffile-prefix-map=/build=.' /build/gcc-14-14.2.0/src/libstdc++-v3/configure \
  --host=x86_64-linux-gnu --prefix=/usr --libdir=/usr/lib/x86_64-linux-gnu \
  --disable-multilib --disable-static --disable-libstdcxx-pch \
  --with-gxx-include-dir=/usr/include/c++/14 --enable-libstdcxx-threads=yes
make -j2
make DESTDIR=/build/libstdcxx-installed install

# Regression tests execute the upstream implementation, including patched headers.
source_dir=/build/gcc-14-14.2.0/src/libstdc++-v3
installed=/build/libstdcxx-installed/usr
new_library="$installed/lib/lib/libstdc++.so.6.0.33"
includes=(-I"$installed/include/c++/14" -I"$installed/include/x86_64-linux-gnu/c++/14")
g++-14 -std=gnu++17 "${includes[@]}" -I"$source_dir/testsuite/util" \
  "$source_dir/testsuite/18_support/new_aligned_wrap.cc" \
  -L"$installed/lib/lib" -Wl,-rpath,"$installed/lib/lib" -o /build/aligned-new-regression
/build/aligned-new-regression
g++-14 -std=gnu++17 "${includes[@]}" -fsanitize=address -fno-omit-frame-pointer \
  "$source_dir/testsuite/ext/pb_ds/regression/priority_queues_erase_if.cc" \
  -L"$installed/lib/lib" -Wl,-rpath,"$installed/lib/lib" -o /build/pbds-regression
ASAN_OPTIONS=detect_leaks=1 /build/pbds-regression
g++-14 -std=gnu++17 "${includes[@]}" -I"$source_dir/testsuite/util" \
  "$source_dir/testsuite/20_util/from_chars/127666.cc" \
  -L"$installed/lib/lib" -Wl,-rpath,"$installed/lib/lib" -o /build/from-chars-regression
# Run the same guard-page program with both actual shared libraries.
ulimit -c 0
set +e
LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu /build/from-chars-regression > /build/from-chars-baseline.log 2>&1
baseline_status=$?
set -e
test "$baseline_status" = 139
/build/from-chars-regression
printf 'from_chars guard-page regression: stable baseline SIGSEGV, patched library passed\n'

# Every versioned public export from the stable library must remain available.
nm -D --defined-only /usr/lib/x86_64-linux-gnu/libstdc++.so.6 | awk '{print $NF}' | sort -u > /build/stdcpp-before.symbols
nm -D --defined-only "$new_library" | awk '{print $NF}' | sort -u > /build/stdcpp-after.symbols
comm -23 /build/stdcpp-before.symbols /build/stdcpp-after.symbols > /build/stdcpp-missing.symbols
test ! -s /build/stdcpp-missing.symbols
strip --strip-unneeded "$new_library"
cd /build
apt-get download libstdc++6=14.2.0-19
dpkg-deb --raw-extract libstdc++6_14.2.0-19_amd64.deb /build/stdcpp-package
install -m 0644 "$new_library" /build/stdcpp-package/usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.33
sed -i 's/^Version: 14.2.0-19$/Version: 14.2.0-19+trip2/' /build/stdcpp-package/DEBIAN/control
cd /build/stdcpp-package
find usr -type f -print0 | sort -z | xargs -0 md5sum > DEBIAN/md5sums
SOURCE_DATE_EPOCH=1794268800 dpkg-deb --root-owner-group --build . /out/libstdc++6_14.2.0-19+trip2_amd64.deb
printf 'libstdc++ upstream regressions and complete exported ABI comparison passed\n'
