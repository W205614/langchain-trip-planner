# OpenSSL 3.5.7 Debian trixie security backport

This build retains Debian's `3.5.7-1~deb13u3` source, packaging, configuration,
and quilt series, and appends five upstream OpenSSL fixes. The source descriptor
and both source archives are SHA-256 pinned in `sources.sha256`. The patches
come from the upstream commits named in `patches/` and are individually pinned
in `patches.sha256`.

The downstream binary package revision is `3.5.7-1~deb13u3+trip1`; it does not
claim to be the Debian `u4` package. The build installs the matching three
runtime packages together: `libssl3t64`, `openssl`, and
`openssl-provider-legacy`. Their SONAMEs and installed runtime tests must be
checked against the pre-backport Debian binaries before this package is used
in a release.

The patches include upstream regressions for the CMS invalid EC group order,
QUIC checked-out stream overlaps, AES-GCM-SIV nonce lengths, and AAD handling.
The OpenSSL 3.5.7 test tree predates some later test anchors; any test-only
context adaptation must remain limited to existing test setup/registration and
retain the upstream regression assertions. Keep failed or inapplicable patch
checks as evidence; do not classify scanner findings as fixed unless the built
image and regression results bind to this recipe.
