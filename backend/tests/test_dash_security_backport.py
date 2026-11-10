"""Opt-in checks against the actual final downstream dash package."""
import hashlib
import importlib.util
import os
from pathlib import Path
import platform
import subprocess

import pytest


pytestmark = pytest.mark.skipif(
    platform.system() != 'Linux' or os.environ.get('TRIP_RUNTIME_DASH_CHECK') != '1',
    reason='Explicit downstream dash runtime checks only',
)

PROVENANCE = Path('/usr/local/share/tripplanner/dash-backport')


def test_installed_dash_matches_the_reviewed_fnmatch_build():
    version = subprocess.check_output(['dpkg-query', '-W', '-f=${Version}', 'dash'], text=True)
    assert version == '0.5.12-12+trip1'
    binary = Path('/bin/dash')
    assert Path('/bin/sh').resolve() == binary.resolve()
    expected, recorded_path = (PROVENANCE / 'dash-sha256.txt').read_text().split()
    assert recorded_path == '/bin/dash'
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == expected
    assert '#define HAVE_FNMATCH 1' in (PROVENANCE / 'config.h').read_text()
    assert ' U fnmatch@' in (PROVENANCE / 'dash.dynamic-symbols.txt').read_text()


def test_real_shell_patterns_are_bounded_and_fourteen_printf_contracts_stay_unchanged():
    spec = importlib.util.spec_from_file_location('dash_runtime_probe', PROVENANCE / 'probe.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.probe(Path('/bin/dash'))
    assert result['bounded_pattern_cases'] == 2
    assert len(result['printf_cases']) == 14
