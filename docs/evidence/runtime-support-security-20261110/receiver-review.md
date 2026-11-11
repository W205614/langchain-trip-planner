# Local supporting runtime review (2026-11-10)

This review covers local support-image maintenance. It does not publish a new
support package or claim that the daily environment has already been upgraded.
The reviewed build snapshot has OCI revision `unknown`: the input files were
reviewed before the final engineering commit. A subsequent revision-label change
must prove identical runtime layers and configuration before reusing this review.

## Receiver: actual fixes and component removal

The canonical application is
[`operational_alert_receiver.py`](../../../backend/scripts/operational_alert_receiver.py).
It uses the Python standard library and writes a bounded local alert inbox. Its
production contract remains UID/GID `1000:1000`, `/inbox/alerts.jsonl`, TCP 9088,
and graceful SIGTERM. No model, map service, database or package installer is
needed by this process. The new public build entry is
[`alert-receiver.Dockerfile`](../../../deploy/images/alert-receiver.Dockerfile),
with the repository root as its Docker build context.

The old daily receiver had 28 raw findings. Merely moving to Python 3.13.18 and
refreshing signed Alpine packages left 14 findings: 11 against SQLite 3.53.4 and
three against the unchanged Python version string. The receiver has no SQLite
consumer. Its rebuilt runtime removes the installed SQLite APK, native library
and aliases, `_sqlite3` extension and `sqlite3` Python package. The APK database
remains genuine: the upstream Python dependency metapackage is removed after the
retained Python library dependencies are explicitly installed. The harmless
`dbm/sqlite3.py` Python wrapper remains; it imports the removed `sqlite3` package
and contains no SQLite implementation. The complete stopped rootfs was examined,
including ELF files, rather than treating a failed `docker cp` as absence.

The remaining three scanner matches are addressed by **real installed backports**,
not execution-path claims or accepted vulnerabilities:

| Finding | Actual replacement and upstream change |
| --- | --- |
| CVE-2026-103995 | `http/client.py`: enforce decimal Content-Length and hexadecimal chunk-size grammar, retaining valid optional whitespace. [2eb2af8](https://github.com/python/cpython/commit/2eb2af805c156ca52b316bb4ced5e0ab3971d0ae), [9f52b44](https://github.com/python/cpython/commit/9f52b443c002cccfb54bda0706a9276b6b4a109d). |
| CVE-2026-116566 | Actual musl `_socket` ELF: reject embedded NUL bytes in host and service strings before passing them to `getaddrinfo`. [131a3a6](https://github.com/python/cpython/commit/131a3a66c5f400b588f9fd2732a5ed4e89623ee3). |
| CVE-2025-15367 | `poplib.py`: reject all C0 and DEL controls before sending POP3 commands. [b234a2b](https://github.com/python/cpython/commit/b234a2b67539f787e191d2ef19a7cbdce32874e7). |

The exact original Python 3.13.18 source, SHA-256
`abb0102e6d4ae027509bcb82327d9dd85515b44a3ba8ad538cfc5e9847029f68`,
and the existing three complete upstream backports are reused unchanged from
[`cpython-backports`](../../../backend/security/cpython-backports/README.md).
The receiver build uses the existing Alpine SDK and the pinned official Python
interpreter, headers and configuration; it compiles a new musl extension. It does
not copy the Debian Agent's extension. Python still honestly reports `3.13.18`.
The removed pip/setuptools distributions are unnecessary for this pure stdlib
receiver; their installed metadata is removed by pip before pip itself is gone.

The build ran the actual upstream `test_socket`, `test_httplib` and `test_poplib`:
all three passed, 965 tests run, 74 platform/resource skips. The actual receiver
image additionally passed five standard-library test methods, including 90
socket/HTTP/POP3 table cases, real HTTP receipt and deduplication, SIGTERM exit 0,
and byte-identical inbox preservation. This test runs nonroot, read-only,
capabilities dropped, no new privileges, and without external networking.
The networkless fixture supplies a local hostname mapping: without one,
`HTTPServer.server_bind` waits in hostname reverse lookup. That original fixture
failure and its stack trace are retained; no application workaround was added.

[`receiver-artifacts.json`](receiver-artifacts.json) records actual installed
ELF/source hashes, architecture, complete APK versions, entrypoint and user.
The original [14-match scan](receiver-before-sqlite-removal-grype.json) and
subsequent [three-match raw scan](receiver-after-sqlite-removal-grype.json) remain
unchanged. A strict source/artifact-bound policy must match these concrete
replacements and Alpine identities before classifying those three as fixed;
unknown IDs, versions, packages or bytes remain blocking.

## Other local supporting images

PostgreSQL retains the official 17.11 server and entrypoint, replaces gosu with
official 1.20 source (which uses `moby/sys/user` 0.4.1), and installs the existing
reviewed BusyBox backport. A real isolated PostgreSQL 17.11 instance passed user
switching, JSONB/identity operations, rollback, and a custom dump/restore equality
check. This is a synthetic compatibility check, not a full daily restore drill.

Prometheus 3.16.0, Alertmanager 0.34.1 and Caddy 2.11.9 retain their reviewed
application sources and standard features. Alertmanager's actual Go-generated
lock raises the affected OpenTelemetry family to 1.45.0. All four supporting
recipes install the existing BusyBox security APKs; common backport inputs were
not modified. The actual images have zero raw findings in the same all-level
Grype database. Prometheus's canonical configuration, 17 alert rules and rule
tests passed; real Alertmanager firing/resolved notifications reached the actual
receiver using the canonical two-minute grouping interval. Caddy's standard ACME
and reverse-proxy modules, canonical configuration adaptation and an isolated
HTTP reverse proxy passed. Public ACME issuance was not attempted.

Raw scans, download failures, fixture failures, successful build logs and local
source/license materials remain in the project release-review evidence directory.
No old support images, daily volumes, networks, backup files or Docker caches
were removed by this work.
