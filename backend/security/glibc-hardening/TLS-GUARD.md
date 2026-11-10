# x86_64 TLS guard: downstream glibc 2.41 rebase

This is a downstream hardening rebase for CVE-2019-1010022, **not an upstream
merged release fix**. It implements the complete `PROT_NONE` / `mprotect`
behavior from Adhemerval Zanella's [2026-05-04 RFC](https://sourceware.org/pipermail/libc-alpha/2026-May/177104.html).
The later [review discussion](https://sourceware.org/pipermail/libc-alpha/2026-May/177501.html)
is retained as context. No allocator replacement, stack-address randomization,
new tunable or public pthread field is introduced here.

## Source identity and license

- RFC original HTML SHA-256:
  `9eeecab6c8439b98b1be0149450242c748b232453fd52ee78f990a9622a0687e`.
- RFC extracted text SHA-256:
  `d8cdc587bda4848c27c500d3f0a862adfbc03ac4027e3c27dbb4f93ef40e3a2c`.
- Rebase input: Debian glibc 2.41 with the project's mature `+trip3` security
  backports, before experimental thread hardening. In the isolated validation,
  its source/build image was
  `sha256:602e8f986b5ca05cb6996a965dc8a64981c7c180cd85b7986cabc5e6a26581dd`.
- The missing upstream support helper comes from official glibc blobs
  [`7df304c80069563982d104f72d0d1eb935ced90b`](https://api.github.com/repos/bminor/glibc/git/blobs/7df304c80069563982d104f72d0d1eb935ced90b)
  (`check_mem_access.h`) and
  [`bf6a7157dcff9c68992f1019f519d21645b45072`](https://api.github.com/repos/bminor/glibc/git/blobs/bf6a7157dcff9c68992f1019f519d21645b45072)
  (`support_mem_access.c`). On 2.41 the latter explicitly uses `_ISOMAC`
  public-header mode: its internal `include/setjmp.h` otherwise binds a
  libc-only hidden `__longjmp_chk` in a nonshared test helper. Fortification,
  test assertions and compiler error checks remain enabled.

The patch and derived regression retain the upstream FSF copyright and
LGPL-2.1-or-later terms. Keep the complete original glibc/Debian source,
project backports, this patch, tests, notices and build instructions in the
corresponding-source distribution. `tls-guard.sha256` pins the patch bytes.

## Exact scope and preserved contracts

Only Linux **x86_64 LP64** enables `ARCH_HAS_TLS_GUARD`; i386/x32 and other
architectures are not claimed tested or hardened. glibc 2.41 lacks the later
`MADV_GUARD_INSTALL` stack-mode infrastructure. This rebase therefore uses the
real `mprotect` fallback on all kernels, including newer ones; it does not
claim to have exercised the lightweight-madvise path.

For glibc-allocated stacks, allocate an additional guard of the existing guard
size below static TLS. All guard rounding and both allocation additions reject
overflow. Only the usable stack and the TLS/TCB regions become readable and
writable. The clone stack top ends at the TLS guard. Cached stack guard growth,
shrink and removal update **both** guard regions, including restoration of the
old ranges when the requested guard becomes zero. VMA names cover the usable
stack rather than counting the new TLS/guard VMAs as extra thread stacks.

`guardsize == 0` disables both guards; caller-provided stacks retain their
original layout and caller-owned protection. Public pthread object layouts,
symbol versions and the RFC's requested stack-size/guard-size reporting are
preserved. The reported range already includes implementation TLS overhead;
the new TLS guard can lie near its high end. Do not equate binary ABI and
reported-size compatibility with a guarantee that arbitrary consumers may
linearly read every byte in that reported range. Actual application/native
library concurrency compatibility must be validated after integration.

The stable internal interfaces for the subsequent stack-address work are:

```c
tls_guard_end_position (const struct pthread *pd,
                        size_t tls_static_size_for_stack,
                        size_t pagesize_m1);
tls_guard_position (const struct pthread *pd,
                    size_t tls_static_size_for_stack,
                    size_t guardsize, size_t pagesize_m1);
```

Apply this patch **before** the independent stack-address/cache hardening.
It deliberately keeps the original cache selection so guard resizing can be
tested with proven reuse. A later no-cache build must not claim to exercise
that reuse path.

## Build and verification

Inside the matching complete Debian glibc build tree, with this directory
available as `/hardening`:

```sh
cd /hardening
sha256sum -c tls-guard.sha256
cd /build/glibc-2.41
patch --batch --fuzz=0 -p1 < /hardening/tls-guard.patch
make -C build-tree/amd64-libc -j2

make -C build-tree/amd64-libc -j2 check subdirs=nptl \
  tests='tst-attr2 tst-attr3 tst-attr4 tst-default-attr tst-stack2 tst-stack3 tst-stack4' \
  tests-internal='tst-tls-guard tst-stackguard1 tst-stackguard1-static' \
  tests-container= tests-special=

# With /proc/self/maps available in the isolated environment, execute the
# unchanged upstream getattr body without another nested chroot.
make -C build-tree/amd64-libc -j2 check subdirs=nptl \
  tests=tst-pthread-getattr tests-internal= tests-container= tests-special=

gcc -O2 -Wall -Wextra -Werror -pthread \
  /hardening/tls-guard-regression.c -o /tmp/tls-guard-regression
build-tree/amd64-libc/elf/ld.so \
  --library-path "$PWD/build-tree/amd64-libc" \
  /tmp/tls-guard-regression --require-cache-reuse
```

The independent regression performs actual read/write fault probes below a
live static-TLS object and above the current stack frame, so an unrelated gap
above the mapping cannot masquerade as the new guard. It checks TLS zero
initialization and preservation, lower and TLS guards, repeated exact cache
reuse across grow/shrink/zero transitions, unaligned guards, caller-owned
stacks, public attribute values and overflow rejection. Against the unpatched
input it must fail because the TLS gap is absent. Run it without the optional
reuse flag on a later build that intentionally disables stack reuse.

The isolated x86_64 prototype passed the 10 directly affected NPTL tests above
and the separate upstream `tst-pthread-getattr` test. Six automatically selected
GDB pretty-printer tests were `UNSUPPORTED` because that SDK has no GDB; they
are not counted as passed. The strict independent regression passed all seven
stack scenarios, verified actual reuse, and rejected guard-size overflow. The
same final C regression failed on the original input specifically at the
missing TLS-gap assertion. All 3,141 defined dynamic symbol/version names
remained identical. The final eight patched source files were matched by hash
against the tested image, including the test-helper compatibility correction.

Final application images still require their own integrated native/Python and
business regressions, provenance binding and package-version update. A passing
source-stage test does not authorize publishing or declare other raw findings
resolved.
