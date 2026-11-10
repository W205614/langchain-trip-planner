"""Observe PAM_RHOST from the real compiled login through a local test spy."""
import os
import pty
import subprocess


environment = dict(os.environ, LD_PRELOAD='/build/login-pam-rhost-regression.so')
for remote in ('remote.example.test', 'remote.other.test', 'remote'):
    environment['TRIP_LOGIN_EXPECT_RHOST'] = remote
    master, slave = pty.openpty()
    try:
        result = subprocess.run(
            ['/build/util-linux-2.41.5/login', '-h', remote, 'trip-test-user'],
            stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, timeout=10, check=False,
        )
        assert result.returncode == 0, (remote, result.returncode, result.stderr.decode())
    finally:
        os.close(slave)
        os.close(master)
print('Actual login PAM_RHOST preserves same-domain FQDN, foreign FQDN and short host')
