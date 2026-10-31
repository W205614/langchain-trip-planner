# Debian 13 native security backports

The Agent stays on Debian 13 amd64 and CPython 3.11.18. The multi-stage Dockerfile
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
| CVE-2026-115557 | [CPython 3.11.18 security release](https://www.python.org/downloads/release/python-31118/), pinned official Python image |

The util-linux patch order is explicit in `util-linux-patches.txt`. Two patches
from the 2.41.6 release are already present in Debian's stable source package and
are not applied twice. The cgroup-FD closure and correct symlink flag are later
official stable-branch fixes; 2.41.6 alone is insufficient for this build.

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
and valid-entry fallback. Its exact installed ELF bytes and `+trip2` glibc package
versions are required by the security assessment; the old image remains blocked
for this advisory. The raw High findings remain in scanner output.

The custom glibc `+trip2` and other native `+trip1` revisions preserve package-manager ownership and
dependency ordering. Future Debian security releases should replace these
backports after the patches, ABI, regressions and fresh image scan are reviewed.
The former eight-CVE risk-acceptance records remain historical evidence; a
remediated image must not inherit their fingerprints or use their exception.
