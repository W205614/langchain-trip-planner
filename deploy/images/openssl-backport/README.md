# Alpine OpenSSL 3.5 backport

`recipe.tar.gz` contains the Alpine 3.24 package recipe and its patches from
aports `0f4c7e8d0f5f7cebbdc2156eca4391080b732bf2`, with the five OpenSSL 3.5
patch files that repair the four advisories below. The recipe retains the
Alpine configuration and test suite. The source tarball and recipe archive
are pinned in `source.lock`. The tested runtime ELF digests are in
`libraries.sha256` and `cli.sha256`.

- CVE-2026-124709 ([upstream commit](https://github.com/openssl/openssl/commit/f3acb0a3328046741479482c51cec815839e1b05))
- CVE-2026-121482 ([upstream commit](https://github.com/openssl/openssl/commit/468c287cf451a834b370b69ab321b8ffdfac23aa), [upstream regression](https://github.com/openssl/openssl/commit/1c65edebefa0dd11aa41cb1a31078ecd9c79d472))
- CVE-2026-107493 ([upstream commit](https://github.com/openssl/openssl/commit/c8f6d04f8e6d40b1bf83334e4a2ec9daf5943a3d))
- CVE-2026-35190 ([upstream commit](https://github.com/openssl/openssl/commit/9491b0d9cc7950af1f3dc89499bd5433b9cd4f43))

The builder signs `3.5.9_p20261121-r0` with a per-build key. Installation verifies
that signature and the installed library hashes. Images with the OpenSSL CLI
install the matching CLI package, and the others keep their existing package
set. The signing key is scoped to the build and install steps. The final
image contains only the source recipe receipt and digest manifests.

The security gate retains Grype's original findings. Its assessment requires
the exact Alpine APK versions, the installed amd64 library bytes, the source
receipt, a package match from the installed APK database, and the scanned
image's source revision. CVE-2026-107494, CVE-2026-84786 and CVE-2026-95690
remain subject to the same blocking policy. The receipt describes a downstream
build. Official Alpine package provenance is preserved for the earlier layers.

Use `python tools/sync_busybox_inputs.py` to update the generated copies for
the frontend and business backend contexts after changing canonical inputs.
