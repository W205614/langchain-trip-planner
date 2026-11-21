# OpenSSL 3.5.9 Debian trixie security backport

This build retains Debian's `3.5.9-1~deb13u1` source, packaging, configuration,
and quilt series, and appends five upstream OpenSSL fixes. The source descriptor, signature,
and both source archives are SHA-256 pinned in `sources.sha256`. The patches
come from the upstream commits named in `patches/` and are individually pinned
in `patches.sha256`.

The downstream binary package revision is `3.5.9-1~deb13u1+trip1`; it does not
claim to be an official Debian package. The build installs the matching three
runtime packages together: `libssl3t64`, `openssl`, and
`openssl-provider-legacy`. Their SONAMEs and installed runtime tests must be
checked against the pre-backport Debian binaries before this package is used
in a release.

The patches include upstream regressions for the CMS invalid EC group order,
QUIC checked-out stream overlaps, AES-GCM-SIV nonce lengths, and AAD handling.
The upstream regression patches apply directly on the Debian 3.5.9 source.
The earlier AAD test adapter is preserved for the 3.5.7 release evidence.
This rebase carries forward the five listed fixes. Later OpenSSL advisories
are assessed separately by the image scanner.
