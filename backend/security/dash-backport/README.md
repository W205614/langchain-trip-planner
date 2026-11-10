# Debian dash 0.5.12 libc-fnmatch rebuild

This downstream build keeps the complete Debian `dash 0.5.12-12` source and
patch series. It changes only Debian's existing `--disable-fnmatch` choice to
the upstream-supported `--enable-fnmatch` configuration. The resulting binary
uses libc `fnmatch`, avoiding the recursive internal `pmatch` path described in
[CVE-2026-102473](https://security-tracker.debian.org/tracker/CVE-2026-102473).
Other Debian features and patches stay unchanged. The exported package is
`dash 0.5.12-12+trip1`; this identifies a real downstream configuration rebuild,
not a claim that Debian has published a fixed release.

Use a Debian 13 amd64 stage with Python and the normal Debian packaging tools.
Copy this directory to `/dash-patches`, run `build-inputs.sh`, then `build.sh`.
The exact source checksums are mandatory. The build runs Debian's packaging
checks, confirms `HAVE_FNMATCH=1` and the actual dynamic import, then executes
two bounded pattern cases and fourteen legacy printf cases before exporting
the package and source/build/binary provenance under `/out`.

CVE-2026-102474 is assessed separately. The retained Debian 0.5.12 implementation
has no Unicode-escape conversion path: single-backslash `\u`/`\U` inputs stay
literal in both format and `%b` paths. Source and Debian patches confirm that
the affected later implementation was not introduced. This is not a source
fix for that CVE and does not add Unicode support or change reservations.

The final image must run `backend/tests/test_dash_security_backport.py` with
`TRIP_RUNTIME_DASH_CHECK=1`, validating the installed package, binary hash,
build provenance and actual behavior. Keep `/bin/sh` pointing to dash.
Keep the full upstream and Debian source, this recipe, source checksums,
copyright/license text and exact modified rules in the corresponding-source
bundle. The `.invalid` maintainer string is a reserved build identity.
