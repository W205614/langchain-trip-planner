#!/bin/bash
set -euo pipefail
cd /patches
sha256sum -c glibc-tests.sha256
cd /build/glibc-2.41
patch --batch --fuzz=0 -p1 < /patches/test-patches/glibc-tests-trixie.patch
build=$PWD/build-tree/amd64-libc
current_test=
report_failure() {
  status=$?
  if [ "$status" != 0 ] && [ -n "$current_test" ]; then
    for suffix in test-result out; do
      test ! -f "$build/$current_test.$suffix" || cat "$build/$current_test.$suffix" >&2
    done
  fi
  exit "$status"
}
trap report_failure EXIT
for current_test in stdlib/tst-strfmon-bug34510 intl/tst-gettext-path-traversal \
  posix/tst-wordexp-append posix/tst-wordexp-tilde \
  resolv/tst-ns_sprintrr resolv/tst-resolv-res_ninit \
  misc/tst-tsearch elf/tst-dl-path-normalize; do
  # The upstream single-test target avoids unrelated sysdeps-added suites.
  make -C "$build" -j2 test "t=$current_test" "tests=${current_test##*/}" tests-container= tests-special=
  grep -qx "PASS: $current_test" "$build/$current_test.test-result"
done
current_test=elf/tst-origin-secure
victim_flags='-Wl,--no-as-needed -Wl,-rpath,\$$ORIGIN/sub/../../../../..$(slibdir)/tst-origin-secure -Wl,--disable-new-dtags'
# Run the isolated fixture directly. Its child must also use the rebuilt
# loader, rather than the system interpreter normally used by child tests.
make -C "$build" -j2 test "t=$current_test" tests=tst-origin-secure \
  tests-internal= tests-container= tests-special= \
  modules-names='libtst-origin-secure-mod tst-origin-secure-evilmod' \
  "LDFLAGS-tst-origin-secure-victim=$victim_flags" \
  "rtld-tests-LDFLAGS=-Wl,-dynamic-linker=$build/elf/ld.so"
readelf -l "$build/elf/tst-origin-secure-victim" \
  | grep -F "Requesting program interpreter: $build/elf/ld.so]"
grep -qx "PASS: $current_test" "$build/$current_test.test-result"
current_test=
gcc -O2 -Wall -Wextra /patches/glibc-boundary-regression.c -lresolv -o /build/glibc-boundary-regression
GCONV_PATH="$build/iconvdata" \
  "$build/elf/ld.so" --library-path "$build:$build/resolv" /build/glibc-boundary-regression
printf 'Required glibc source regressions and actual boundary checks passed\n'
