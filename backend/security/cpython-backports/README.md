# CPython 3.13.18 downstream security backports

The runtime uses the official Docker Python 3.13.18 executable and rebuilds
its shared library from the same release. The source archive is pinned to SHA-256
`abb0102e6d4ae027509bcb82327d9dd85515b44a3ba8ad538cfc5e9847029f68`.
The build replaces `_socket`, `http.client`, `http.cookiejar`, `http.cookies`,
`imaplib`, `poplib` and `ssl` from that exact source plus these upstream changes:

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
* CVE-2026-124249: [8eb3d2c](https://github.com/python/cpython/commit/8eb3d2c1c41ca7ce628554413981c1eb763a1494)
  applies cookie expiry after the acceptance policy decision.
* CVE-2026-19552: [8dde46e](https://github.com/python/cpython/commit/8dde46eaa6baf5f027a7234845b20d50b509624e)
  verifies the host identity when `get_server_certificate` receives a CA file.
* CVE-2026-16052: [2702684](https://github.com/python/cpython/commit/27026842f6774c167295f4b59972589f12ec5987)
  quotes cookie attributes that contain separators. The build retains the
  entire upstream patch and applies its runtime, test and NEWS hunks.
* CVE-2026-115559: [2f0e294](https://github.com/python/cpython/commit/2f0e294d85c905691e360586ebd1d1f7e7a1f5df)
  clears cached untagged responses when STARTTLS succeeds. The 3.13.18
  implementation replaces its buffered file at this transition, so the local
  patch applies the merged cache reset at that point. The official 3.13
  backport is tracked in [#161759](https://github.com/python/cpython/pull/161759).
* CVE-2026-115791: [8aa5d65](https://github.com/python/cpython/commit/8aa5d657e4d3f1a4cca5ff6c976e2e868e0a9056)
  records the interpreter's startup path and removes it from the archive CLIs.
  The patch keeps the merged 3.14 runtime changes and adjusts the three CLI
  hunks to match 3.13.18. The runtime uses the rebuilt `libpython`, which
  contains frozen `runpy`, and the patched on-disk standard library modules.

The patches contain PSF-licensed upstream code. The complete original source,
patches and build instructions must remain in the corresponding-source bundle.
The shared-library target uses the official build's configure options, LTO,
[Debian compiler and linker flags](https://github.com/docker-library/python/blob/master/3.13/slim-trixie/Dockerfile),
and [Alpine thread stack size](https://github.com/docker-library/python/blob/master/3.13/alpine3.24/Dockerfile).
The Debian profiling run is outside that target. The build compares exported
Python symbols, SONAME, needed libraries and ELF protections with the official
library before installation. It runs upstream archive and interpreter tests
alongside the socket, HTTP, cookie, IMAP, POP3 and TLS tests. Runtime provenance records the
installed file hashes and Python retains its upstream version string.
