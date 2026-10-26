"""Build-only, tightly pinned Debian ABI-compatible library security updates.

This is not a rolling unstable upgrade. Only two authenticated, hash-pinned .deb
files may change; the simulated and actual complete package delta must match.
The temporary signed repository and negative-priority pin are removed in-image.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile


def run(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        print(result.stdout + result.stderr, file=sys.stderr, flush=True)
        raise RuntimeError('Reviewed runtime package command failed: ' + args[0])
    return result.stdout


def installed():
    return dict(line.split('\t', 1) for line in run('dpkg-query', '-W', '-f=${Package}\t${Version}\n').splitlines())


def validate_simulation(text, expected):
    changed = {line.split()[1].split(':')[0] for line in text.splitlines() if line.startswith('Inst ')}
    if changed != expected or any(line.startswith('Remv ') for line in text.splitlines()):
        raise RuntimeError('APT simulation proposed changes outside the exact two reviewed libraries')
    count = re.search(r'(\d+) upgraded, (\d+) newly installed, (\d+) to remove', text)
    if not count or tuple(map(int, count.groups())) != (2, 0, 0):
        raise RuntimeError('APT simulation was not exactly two upgrades with no new or removed packages')


def apply(lock_path):
    if os.name != 'posix' or os.geteuid() != 0 or os.environ.get('TRIP_SECURITY_IMAGE_BUILD') != '1':
        raise RuntimeError('Only the explicitly selected root Docker build step may apply these updates')
    lock = json.loads(lock_path.read_text())
    if datetime.now(timezone.utc).date().isoformat() > lock['review_expires_on']:
        raise RuntimeError('Runtime package assessment expired; recheck vendor stable fixes before building')
    release = Path('/usr/lib/os-release').read_text()
    if 'ID=debian' not in release or 'VERSION_ID="13"' not in release or run('dpkg', '--print-architecture').strip() != 'amd64':
        raise RuntimeError('Runtime package lock applies only to the reviewed Debian 13 amd64 ABI')
    before = installed()
    for name, version in lock['requires_base_versions'].items():
        if before.get(name) != version:
            raise RuntimeError('Base package changed; re-evaluate the patch instead of forcing a downgrade: ' + name)
    source = Path('/etc/apt/sources.list.d/trip-security-libraries.list')
    priority = Path('/etc/apt/preferences.d/trip-security-libraries')
    if source.exists() or priority.exists():
        raise RuntimeError('Unexpected pre-existing security patch repository configuration')
    source.write_text('deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian sid main\n')
    priority.write_text('Package: *\nPin: release n=sid\nPin-Priority: -1\n')
    try:
        run('apt-get', 'update')  # APT verifies official InRelease with Debian archive keys.
        requested = [name + '=' + entry['version'] for name, entry in lock['packages'].items()]
        simulation = run('apt-get', '--simulate', '--no-install-recommends', '--no-remove', 'install', *requested)
        print(simulation, flush=True)
        validate_simulation(simulation, set(lock['packages']))
        with tempfile.TemporaryDirectory(prefix='trip-security-libraries-') as folder:
            os.chmod(folder, 0o755)
            run('apt-get', 'download', *requested, cwd=folder)
            archives = sorted(Path(folder).glob('*.deb'))
            if len(archives) != len(lock['packages']):
                raise RuntimeError('Unexpected downloaded package set')
            observed = set()
            for archive in archives:
                fields = run('dpkg-deb', '--field', str(archive), 'Package', 'Version', 'Architecture').splitlines()
                metadata = dict(line.split(': ', 1) for line in fields)
                name = metadata.get('Package'); entry = lock['packages'].get(name)
                if not entry or metadata.get('Version') != entry['version'] or metadata.get('Architecture') != 'amd64':
                    raise RuntimeError('Downloaded package identity differs from lock')
                if hashlib.sha256(archive.read_bytes()).hexdigest() != entry['sha256']:
                    raise RuntimeError('Downloaded package failed reviewed signed-index SHA256')
                payload = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', str(archive)])
                with tarfile.open(fileobj=io.BytesIO(payload)) as files:
                    if not any(Path(member.name).name == entry['soname'] and member.issym() for member in files.getmembers()):
                        raise RuntimeError('Library does not preserve reviewed SONAME: ' + name)
                observed.add(name)
            if observed != set(lock['packages']):
                raise RuntimeError('Library package set differs from lock')
            run('apt-get', 'install', '-y', '--no-install-recommends', '--no-remove', *map(str, archives))
        after = installed()
        changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
        if changed != set(lock['packages']) or any(after.get(name) != entry['version'] for name, entry in lock['packages'].items()):
            raise RuntimeError('Installed package delta exceeded the exact library lock')
        print(json.dumps({'status': 'applied', 'changed_packages': {name: after[name] for name in sorted(changed)},
                          'libc_unchanged': after.get('libc6') == before.get('libc6')}), flush=True)
    finally:
        source.unlink(missing_ok=True)
        priority.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, required=True)
    apply(parser.parse_args().lock)
