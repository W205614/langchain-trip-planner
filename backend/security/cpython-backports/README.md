# CPython 3.13.18 downstream security backports

The interpreter remains the official Docker Python 3.13.18 build. Its release
source archive is pinned to SHA-256
`abb0102e6d4ae027509bcb82327d9dd85515b44a3ba8ad538cfc5e9847029f68`.
Only `http.client`, `poplib` and the `_socket` extension are replaced with builds from
that exact source plus these upstream changes:

* CVE-2026-103995: [2eb2af8](https://github.com/python/cpython/commit/2eb2af805c156ca52b316bb4ced5e0ab3971d0ae)
  and the optional-whitespace correction
  [9f52b44](https://github.com/python/cpython/commit/9f52b443c002cccfb54bda0706a9276b6b4a109d).
* CVE-2026-116566: [131a3a6](https://github.com/python/cpython/commit/131a3a66c5f400b588f9fd2732a5ed4e89623ee3).
  The backport retains 3.13's existing argument parsing and adds the upstream
  length checks without changing its public signatures or generated headers.
* CVE-2025-15367: [b234a2b](https://github.com/python/cpython/commit/b234a2b67539f787e191d2ef19a7cbdce32874e7)
  rejects C0/DEL control characters in POP3 commands before sending any bytes.
  The exact upstream patch is retained as `upstream-poplib-b234a2b.patch`.
  `CVE-2025-15367.patch` rebases only the test import context to retain 3.13.18's
  existing `ExtraAssertions` import; runtime changes, regression statements and
  the upstream NEWS fragment remain unchanged. Both patch hashes are checked,
  and only the rebased patch is applied.

The patches contain PSF-licensed upstream code. The complete original source,
patches and build instructions must remain in the corresponding-source bundle.
The build runs the upstream socket, HTTP client and POP3 tests before exporting
the three replacements. Runtime provenance records the patched files' hashes; the
official version string is not changed to imply an upstream release fix.
