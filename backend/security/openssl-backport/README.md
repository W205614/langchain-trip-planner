# OpenSSL 3.5.9 Debian trixie security backport

This build retains Debian's `3.5.9-1~deb13u1` source, packaging, configuration,
and quilt series, and appends nine upstream OpenSSL fixes. The source descriptor, signature,
and both source archives are SHA-256 pinned in `sources.sha256`. The patches
come from the upstream commits named in `patches/` and are individually pinned
in `patches.sha256`.

The downstream binary package revision is `3.5.9-1~deb13u1+trip2`. The build installs the matching three
runtime packages together: `libssl3t64`, `openssl`, and
`openssl-provider-legacy`. Their SONAMEs and installed runtime tests must be
checked against the pre-backport Debian binaries before this package is used
in a release.

The patches include upstream regressions for the CMS invalid EC group order,
QUIC checked-out stream overlaps, AES-GCM-SIV nonce lengths, and AAD handling.
The upstream regression patches apply directly on the Debian 3.5.9 source.
The earlier AAD test adapter is preserved for the 3.5.7 release evidence.
`+trip2` also carries four repairs merged into the OpenSSL 3.5 branch.

- CVE-2026-124709. [OpenSSL change](https://github.com/openssl/openssl/commit/f3acb0a3328046741479482c51cec815839e1b05)
- CVE-2026-121482. [OpenSSL change](https://github.com/openssl/openssl/commit/468c287cf451a834b370b69ab321b8ffdfac23aa) and [regression](https://github.com/openssl/openssl/commit/1c65edebefa0dd11aa41cb1a31078ecd9c79d472)
- CVE-2026-107493. [OpenSSL change and regression](https://github.com/openssl/openssl/commit/c8f6d04f8e6d40b1bf83334e4a2ec9daf5943a3d)
- CVE-2026-35190. [OpenSSL change](https://github.com/openssl/openssl/commit/9491b0d9cc7950af1f3dc89499bd5433b9cd4f43)
