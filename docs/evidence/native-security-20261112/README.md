# OpenSSL downstream security backport (2026-11-12)

The Agent keeps Debian trixie OpenSSL 3.5.7 source and packaging and applies the five pinned upstream security fixes in `backend/security/openssl-backport/`. The downstream version is `3.5.7-1~deb13u3+trip1`, not an official Debian u4 package. All three runtime packages are installed together.

The complete Debian package test suite, IPv4/IPv6 BIO negative and positive controls, SONAME and exported-symbol comparisons, and default/legacy provider checks passed during the package build. The final restricted application image retains the patched libraries and removes the unused OpenSSL CLI under the existing runtime-tool policy. The opt-in final-image test checks package revisions, exact library hashes, Python TLS linkage and actual provider loading; it passed in an isolated container with no network.

The local complete Agent image gate passed across all severities: 222 raw matches, 0 unresolved, 15 exact OpenSSL backport assessments, 0 risk acceptances. Raw scanner records and failed attempts remain retained locally. This is local candidate evidence, not a claim that a new public release or remote CI has passed.

[Manifest delta review](openssl-manifest-delta-review.json) compares the previous published Agent with the new candidate. Removed-tool entries and retained-library fingerprints match exactly; only the package database fingerprint changes, with exactly the three OpenSSL records changed. The active assessment still checks exact package versions, retained ELF hashes, absent CLI paths and source bindings.

The changed backend tree invalidates the previous CodeQL source-context review. Fresh official source analysis and a new exact review are required before publication. Previous 11 reviewed Java findings are not automatically transferred to this tree.
