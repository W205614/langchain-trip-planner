# Debian 13 native security backports

The Agent stays on Debian 13 amd64. Its native build toolchain is kept separate
from the application Python runtime. The multi-stage Dockerfile
builds the existing stable source packages with upstream security fixes. Only
runtime `.deb` packages reach the final image; compilers, development headers and
downloaded source archives remain in build stages.

`sources.sha256` records the exact source archives verified against Debian's
signed source indexes on 2026-10-30. `patches.sha256` binds the upstream patches
and the explicitly rebased glibc patch. A failed download, hash check, patch,
regression or ABI comparison fails the build.

| Advisory | Implementation / primary evidence |
| --- | --- |
| CVE-2026-19499 | [glibc b090cf2](https://sourceware.org/git/?p=glibc.git;a=commit;h=b090cf226ff65b913e41536f1f573f500855615c), including upstream bug34510 regression |
| CVE-2026-5435 | [glibc ca44a66](https://sourceware.org/git/?p=glibc.git;a=commit;h=ca44a6609c29a683b03575fa035c6d17aa591e72); the trixie patch preserves Debian's existing unknown-record diagnostic and changes only the upstream printer removals |
| CVE-2026-84243 | [Reviewed upstream v7 submission](https://inbox.sourceware.org/libc-alpha/20261012112453.47383-1-avinal.xlvii@gmail.com/): reject path separators and bare `..` in each LANGUAGE entry; retain the upstream guard and regression bytes, rebase only the stable Makefile context and omit newer-version NEWS |
| CVE-2026-95619 | [GCC 14 branch 200183d](https://github.com/gcc-mirror/gcc/commit/200183d4ab5d57575f3dfebc6af3baf649910b9e), upstream aligned-new regression and comparison of every existing public export |
| CVE-2026-102010 | [GCC aaa8351](https://github.com/gcc-mirror/gcc/commit/aaa8351f4d2e636f9680a1f0a8ebc2f0a60611e6), upstream PBDS regression compiled with patched headers under AddressSanitizer; see the runtime scope below |
| CVE-2026-76642, CVE-2026-78408, CVE-2026-78410 | [util-linux stable 2.41.6 fixes](https://www.kernel.org/pub/linux/utils/util-linux/v2.41/v2.41.6-ReleaseNotes), with the stable branch's missing-include, complete cgroup-FD closure, idmap feature guard and correct `RESOLVE_NO_SYMLINKS` follow-ups |
| CVE-2026-78409 | [Upstream applicability](https://github.com/util-linux/util-linux/security/advisories/GHSA-8f2p-47x3-43mv): the affected detached-subdirectory path starts in 2.42; the maintained 2.41 branch does not include that path |
| CVE-2026-115557 | The application now uses pinned CPython 3.13.18, retaining the upstream security fix; see the separate `cpython-backports` inputs for its additional socket and HTTP fixes. The native build SDK's Python version is not the application interpreter. |
| CVE-2026-6238 | [glibc a7b60d2](https://github.com/bminor/glibc/commit/a7b60d23bbb56eaef59f4962e4140062e552600a), its `inet_ntop` prerequisite and systematic input/output guard-page regression |
| CVE-2026-6368 | [glibc e2cefe1](https://github.com/bminor/glibc/commit/e2cefe16c37a617df9f11407cb00a272a6098823) and [53ec26f follow-up](https://github.com/bminor/glibc/commit/53ec26f1736aee747b353aaea0667b1ebdd5cae7), preserving wordexp state after failed append |
| CVE-2026-6791 | [glibc 07c24f3](https://github.com/bminor/glibc/commit/07c24f35392b727e6100d33edfdf811a6c68c218), retaining the upstream small-stack tilde-expansion regression |
| CVE-2026-77117, CVE-2026-80489 | [Upstream glibc announcement](https://www.openwall.com/lists/oss-security/2026/09/14/31), exact JIS pending-state resets and an incremental-conversion regression |
| CVE-2026-18374 | [glibc 9765a53](https://github.com/bminor/glibc/commit/9765a538ebf8661a6e5578e01e35a3dd30db7eb4), rejecting empty stripped `fopen` charset names while retaining valid UTF-8 |
| CVE-2026-19542 | [glibc e2789c4](https://github.com/bminor/glibc/commit/e2789c46e3bfdcd67a82bea9946b315c179e83d3), bounded tdelete parent stack and existing tree regressions |
| CVE-2026-8674 | [glibc 506ea57](https://github.com/bminor/glibc/commit/506ea57086bfb9ce3daff1c14246a1cb532aba0a), correct search-list truncation and upstream resolver cases |
| CVE-2026-86805, CVE-2026-95818 | [glibc ed0c137](https://github.com/bminor/glibc/commit/ed0c137b97eb940b4b64981e84ed806d3276edd9), normalize the actual trusted ORIGIN lookup path in place, retaining upstream guard-page and secure-loader test source |
| CVE-2026-3184 | [util-linux 8b29aeb](https://github.com/util-linux/util-linux/commit/8b29aeb081e297e48c4c1ac53d88ae07e1331984); the build requires the compiled `login` to pass its original FQDN to a test-only PAM observer |
| GCC PR127666 / CVE-2026-115456 | [GCC 14 official announcement](https://gcc.gnu.org/pipermail/libstdc%2B%2B-cvs/2026q4/045443.html), 0a038d4, with the same guard-page program against the original and rebuilt shared library. The CVE-to-PR association is inferred from the identical non-NUL floating-point defect in [the CNA report](https://access.redhat.com/security/cve/CVE-2026-115456), which does not publish that cross-reference. |

The glibc and util-linux patch orders are explicit in `glibc-patches.txt` and
`util-linux-patches.txt`. Two util-linux patches
from the 2.41.6 release are already present in Debian's stable source package and
are not applied twice. The cgroup-FD closure and correct symlink flag are later
official stable-branch fixes; 2.41.6 alone is insufficient for this build.

The ORIGIN fix is rebased only around the older loader normalizer and Makefile
positions. Its new normalization helper and predicate are the upstream code.
`test-patches/glibc-tests-trixie.patch` keeps Debian's existing unknown-RR diagnostic in
the resolver expectations and supplies the leading-guard allocator missing
from 2.41's test helpers. All upstream boundary checks remain active. The
global raw patch hashes remain in `patches.sha256`. Each native stage checks its
component manifest (`glibc-build.sha256`, `libstdcxx-patches.sha256` or
`util-linux-patches.sha256`); the later glibc check stage verifies its independent
`glibc-tests.sha256`. These manifests partition the same reviewed patch inputs.

The ordinary backport build finishes in `glibc-compiled`. The application uses
`glibc-hardened-compiled`, which applies the separately reviewed TLS, thread-stack
and thread-arena changes from `../glibc-hardening/` and builds real `+trip4`
packages from a fresh source tree. Compilation, packaging and exported ABI
comparison precede the check stage. The application copies packages only from the later
`glibc-backport` stage, after its required tests pass. Test-fixture changes do
not require compiling the unchanged C library again. `make test t=...`, the
upstream single-test target, avoids accidentally running unrelated sysdeps-added
suites. Namespace-dependent resolver variants require an appropriate test
environment; they are distinct from the ordinary process-level long-LOCALDOMAIN
boundary regression and must not be reported as passed when unsupported.

`build-inputs.sh --dependencies-only` and `--sources-only /native-source-cache`
separate the toolchain layer from an optional BuildKit source-download cache.
All nine source files must still match `sources.sha256`; only verified source
archives are copied into a fresh non-cached `/build` tree before extraction.
An existing patched build tree is never reused as an unchecked source input.

PBDS `binary_heap::erase_if` is implemented in development headers, not in the
Debian runtime libraries matched by the scanner. The final image contains no
C++ development headers or compiler. This supports a component-absence assessment
for those exact runtime-package findings, rather than an assertion that replacing
`libstdc++.so` repairs header templates in existing binaries. Prebuilt Python
wheels are not rebuilt by this process. An ELF string/symbol inventory is only
supplementary evidence and cannot prove absence of all inlined template code.

As of 2026-10-31, Debian still reports CVE-2026-84243 as unfixed. This is a local
source backport of the reviewed upstream submission, not a claim of an official
Debian security release. The original native library fails four traversal cases;
the patched build must pass the same upstream test, including ordinary translation
and valid-entry fallback. Its exact installed ELF bytes and reviewed glibc package
revision are required by the security assessment; the older unpatched image remains blocked
for this advisory. The raw High findings remain in scanner output.

The current custom glibc `+trip4`, libstdc++ `+trip2` and util-linux `+trip2`
revisions preserve package-manager ownership and
dependency ordering. Future Debian security releases should replace these
backports after the patches, ABI, regressions and fresh image scan are reviewed.
The former eight-CVE risk-acceptance records remain historical evidence; a
remediated image must not inherit their fingerprints or use their exception.

The three additional 2019 hardening advisories are handled by actual downstream
code changes, not by the former risk acceptance. Their supported platform,
kernel entropy requirement, failure behavior and performance tradeoff are
described in [the runtime notes](../../../docs/operations/native-runtime.md).
They are not represented as an official upstream or Debian security release.
