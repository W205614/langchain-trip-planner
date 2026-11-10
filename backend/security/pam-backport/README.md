# Debian PAM 1.7.0 security backport

This rebuild retains the complete Debian `pam 1.7.0-5` source and Debian patch
series. It applies the complete upstream Linux-PAM commit
[`30708d973b63891bf700299ce3ae0f1086398284`](https://github.com/linux-pam/linux-pam/commit/30708d973b63891bf700299ce3ae0f1086398284)
for CVE-2026-54411, including its constant-time case-insensitive helper,
plaintext and `key_only` paths, bounded database-string copies and erasure.
The source and patch hashes are pinned in the two checksum manifests.

Use a Debian 13 amd64 build stage with Python, compiler and Debian packaging
tools. Copy this directory to `/pam-patches`, run `build-inputs.sh`, then
`build.sh`. The input step downloads the pinned Debian source package; existing
identical source files in `/build` may be reused after mandatory hash checks.
The build keeps Debian's feature flags and runs its upstream tests. It exports
`libpam0g`, `libpam-modules` and `libpam-modules-bin` as `1.7.0-5+trip1` in
`/out/packages`. The unchanged architecture-independent `libpam-runtime`
configuration package is not relabeled as fixed code.

The prototype/runtime acceptance must run
`backend/tests/test_pam_security_backport.py` with `TRIP_RUNTIME_PAM_CHECK=1`.
That test uses real Berkeley DB files, `pam_start_confdir` and the actual
`pam_userdb.so`; it exercises normal, case-insensitive, key-only and encrypted
authentication, including wrong prefixes, lengths and unknown users. A build
may select its newly built module with `TRIP_PAM_USERDB_MODULE`; the default is
the installed Debian module. These functional tests do not claim a statistical
timing guarantee: that property is supported by the complete upstream patch.

Retain the original source archives, Debian source/signature/recipe, this patch,
build/test instructions, package/module hashes and original copyright/license
notices in the corresponding-source distribution. This is a downstream rebuild,
not an upstream or Debian security release. The `.invalid` build identity in
the changelog is a reserved build label, not a support address.
