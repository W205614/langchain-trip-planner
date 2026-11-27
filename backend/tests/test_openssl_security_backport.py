from pathlib import Path
import ctypes
import hashlib
import os
import ssl
import subprocess
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get('TRIP_RUNTIME_ABI_CHECK') != '1',
    reason='Explicit final-image OpenSSL runtime checks only',
)

PROVENANCE = Path('/usr/local/share/tripplanner/openssl-backport')
EXPECTED_VERSION = '3.5.9-1~deb13u1+trip5'


def test_openssl_source_backport_runtime_and_abi_proof():
    for package in ('libssl3t64', 'openssl', 'openssl-provider-legacy'):
        actual = subprocess.check_output(
            ['dpkg-query', '-W', '-f=${Version}', package], text=True
        ).strip()
        assert actual == EXPECTED_VERSION

    assert (PROVENANCE / 'runtime-regressions.txt').read_text().startswith(
        'IPv4/IPv6 peer detection, package installation, SONAME and exported ABI checks passed'
    )
    assert (PROVENANCE / 'dgram-peer-baseline-result.txt').read_text().startswith(
        'unpatched IPv4/IPv6 BIO datagram peer regression failed as expected'
    )
    assert (PROVENANCE / 'dgram-peer-baseline.log').is_file()
    for library in ('libcrypto', 'libssl'):
        before = (PROVENANCE / f'{library}-before.symbols').read_text()
        after = (PROVENANCE / f'{library}-after.symbols').read_text()
        assert before == after

    assert not Path('/usr/bin/openssl').exists()
    expected = {
        'libcrypto.so.3': 'ab3eeb4f433446fed97889edd96bc6e07dbc439904d252cbc518dac6f536aa0e',
        'libssl.so.3': 'c4f5c002a2d1dc72db2634a8c088f1afcaa2a057ad5ada4dcb78f681bb499c12',
        'ossl-modules/legacy.so': '47af7436924d44b49719792780eaee3c472df720908cb969f00c940c219cba45',
    }
    for name, digest in expected.items():
        assert hashlib.sha256((Path('/usr/lib/x86_64-linux-gnu') / name).read_bytes()).hexdigest() == digest
    assert ssl.OPENSSL_VERSION.startswith('OpenSSL 3.5.9 ')
    crypto = ctypes.CDLL('libcrypto.so.3')
    crypto.OSSL_PROVIDER_load.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    crypto.OSSL_PROVIDER_load.restype = ctypes.c_void_p
    crypto.OSSL_PROVIDER_unload.argtypes = [ctypes.c_void_p]
    crypto.OSSL_PROVIDER_unload.restype = ctypes.c_int
    for name in (b'default', b'legacy'):
        provider = crypto.OSSL_PROVIDER_load(None, name)
        assert provider, name
        assert crypto.OSSL_PROVIDER_unload(provider) == 1


def test_openssl_block_encoder_keeps_valid_output_and_validates_length():
    crypto = ctypes.CDLL('libcrypto.so.3')
    crypto.EVP_EncodeBlock.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    crypto.EVP_EncodeBlock.restype = ctypes.c_int
    crypto.ERR_clear_error.argtypes = []
    crypto.ERR_clear_error.restype = None
    crypto.ERR_get_error.argtypes = []
    crypto.ERR_get_error.restype = ctypes.c_ulong

    output = ctypes.create_string_buffer(5)
    assert crypto.EVP_EncodeBlock(output, b'abc', 3) == 4
    assert output.value == b'YWJj'
    crypto.ERR_clear_error()
    assert crypto.EVP_EncodeBlock(output, b'abc', -1) == 0
    assert output.value == b'YWJj'
    assert crypto.ERR_get_error()
