#!/bin/sh
set -eu
cd /tmp/Python-3.13.18
export PYTHONPATH="$PWD/Lib"

# Some standard-library tests call archive main() in the test runner itself.
# Use safe-path mode for the runner so those calls leave its sys.path intact.
python -P -m test -j2 \
    test_socket test_httplib test_imaplib test_poplib test_http_cookiejar test_http_cookies test_ssl \
    test_gzip test_tarfile test_zipfile test_runpy test_frozen

# These launcher cases also run against the official slim image's layout.
python -m test --match test_run_module --match test_run_module_bug1764407 \
    --match test_isolatedmode --match test_argv0_normalization test_cmd_line
