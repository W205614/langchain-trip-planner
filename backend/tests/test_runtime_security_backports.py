"""Run explicitly against the rebuilt Debian image; no provider calls."""
import ctypes
import errno
import os
from pathlib import Path
import platform
import subprocess

import pytest


pytestmark = pytest.mark.skipif(
    platform.system() != 'Linux' or os.environ.get('TRIP_RUNTIME_ABI_CHECK') != '1',
    reason='Explicit native security backport image checks only',
)


def test_runtime_uses_stable_backports_and_fixed_python():
    assert platform.python_version() == '3.13.18'
    revisions = subprocess.check_output(
        ['dpkg-query', '-W', '-f=${Package}\t${Version}\n',
         'libc6', 'libc-bin', 'libstdc++6', 'libmount1', 'util-linux'], text=True)
    assert dict(line.split('\t') for line in revisions.splitlines()) == {
        'libc6': '2.41-12+deb13u4+trip4',
        'libc-bin': '2.41-12+deb13u4+trip4',
        'libstdc++6': '14.2.0-19+trip2',
        'libmount1': '2.41.5-0+deb13u1+trip2',
        'util-linux': '2.41.5-0+deb13u1+trip2',
    }
    assert not Path('/usr/include/c++').exists()
    assert all(not Path('/usr/bin', name).exists() for name in ['mount', 'umount', 'nsenter', 'unshare'])


def test_strfmon_right_padding_stays_within_the_output_buffer():
    # The upstream bug34510 case uses a guard page after a 100-byte buffer.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, ctypes.c_long]
    libc.mmap.restype = ctypes.c_void_p
    libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    libc.strfmon.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p]
    libc.strfmon.restype = ctypes.c_ssize_t
    page = os.sysconf('SC_PAGESIZE')
    address = libc.mmap(None, page * 2, 3, 0x22, -1, 0)
    assert address != ctypes.c_void_p(-1).value
    try:
        assert libc.mprotect(address + page, page, 0) == 0
        assert libc.strfmon(address + page - 100, 100, b'%100n', ctypes.c_double(1.23)) == -1
        assert ctypes.get_errno() == errno.E2BIG
    finally:
        assert libc.munmap(address, page * 2) == 0


@pytest.mark.parametrize('record_type', [37, 249, 250, 41])
def test_deprecated_resolver_printers_use_bounded_unknown_record_output(record_type):
    resolver = ctypes.CDLL('libresolv.so.2')
    printer = resolver.ns_sprintrrf
    printer.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p,
                       ctypes.c_int, ctypes.c_int, ctypes.c_ulong,
                       ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p,
                       ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]
    printer.restype = ctypes.c_int
    data = ctypes.create_string_buffer(b'\0' * 16)
    output = ctypes.create_string_buffer(512)
    size = printer(data, 16, b'example.test.', 1, record_type, 0,
                   data, 16, None, None, output, len(output))
    assert 0 < size < len(output)
    assert b'\\# 16' in output.value


@pytest.mark.parametrize('mode', [b'r,ccs=', b'r,ccs= ', b'r,ccs=  '])
def test_fopen_rejects_an_empty_stripped_charset(mode):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.fopen.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    libc.fopen.restype = ctypes.c_void_p
    assert not libc.fopen(b'/dev/null', mode)
    assert ctypes.get_errno() == errno.EINVAL


@pytest.mark.parametrize('encoding,source', [
    (b'SHIFT_JISX0213', b'\x82\xf5A'),
    (b'EUC-JISX0213', b'\xa4\xf7A'),
])
def test_iconv_emits_a_pending_jis_character_only_once(encoding, source):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.iconv_open.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    libc.iconv_open.restype = ctypes.c_void_p
    libc.iconv.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p),
                           ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_char_p),
                           ctypes.POINTER(ctypes.c_size_t)]
    libc.iconv.restype = ctypes.c_size_t
    libc.iconv_close.argtypes = [ctypes.c_void_p]
    converter = libc.iconv_open(b'UTF-32LE', encoding)
    assert converter != ctypes.c_void_p(-1).value
    remaining = ctypes.c_size_t(len(source))
    current = ctypes.c_char_p(source)
    points = []
    try:
        for index in range(4):
            output = ctypes.create_string_buffer(4)
            pointer = ctypes.cast(output, ctypes.c_char_p)
            available = ctypes.c_size_t(4)
            result = libc.iconv(converter, ctypes.byref(current), ctypes.byref(remaining),
                                ctypes.byref(pointer), ctypes.byref(available))
            assert remaining.value == (1 if index < 2 else 0)
            if index < 2:
                assert result == ctypes.c_size_t(-1).value
                assert ctypes.get_errno() == errno.E2BIG
            else:
                assert result == 0
            assert available.value == (4 if index == 3 else 0)
            points.append(int.from_bytes(output.raw, 'little'))
        assert points == [0x304B, 0x309A, 0x41, 0]
    finally:
        assert libc.iconv_close(converter) == 0
