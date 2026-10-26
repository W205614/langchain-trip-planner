"""Targeted Linux candidate checks for the two reviewed native library updates."""
import ctypes
from io import BytesIO
import os
import platform
import subprocess
import zlib

import pytest


pytestmark = pytest.mark.skipif(platform.system() != 'Linux' or os.environ.get('TRIP_RUNTIME_ABI_CHECK') != '1',
                               reason='Explicit reviewed Debian runtime image ABI check only')


def test_exact_native_library_revisions_keep_zlib_and_acl_abi_compatible():
    revisions = subprocess.check_output(
        ['dpkg-query', '-W', '-f=${Package}\t${Version}\n', 'libacl1', 'zlib1g'], text=True)
    assert dict(line.split('\t') for line in revisions.splitlines()) == {
        'libacl1': '2.4.0-1', 'zlib1g': '1:1.3.dfsg+really1.3.2-4'}
    library = ctypes.CDLL('libz.so.1')
    library.zlibVersion.restype = ctypes.c_char_p
    assert library.zlibVersion().decode() == zlib.ZLIB_RUNTIME_VERSION == '1.3.2'
    payload = bytes(range(256)) * 1024
    compressed = zlib.compress(payload, level=9)
    assert len(compressed) < len(payload)
    assert zlib.decompress(compressed) == payload
    acl = ctypes.CDLL('libacl.so.1')
    acl.acl_from_text.argtypes = [ctypes.c_char_p]
    acl.acl_from_text.restype = ctypes.c_void_p
    acl.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)]
    acl.acl_to_text.restype = ctypes.c_void_p
    acl.acl_free.argtypes = [ctypes.c_void_p]
    value = acl.acl_from_text(b'user::rw-,group::r--,other::---')
    assert value
    text = None
    try:
        size = ctypes.c_ssize_t()
        text = acl.acl_to_text(value, ctypes.byref(size))
        assert text and b'user::rw-' in ctypes.string_at(text, size.value)
    finally:
        if text:
            acl.acl_free(text)
        acl.acl_free(value)


def test_native_image_and_planning_consumers_still_load_without_provider_calls():
    from PIL import Image
    import onnxruntime
    from ortools.sat.python import cp_model
    output = BytesIO()
    Image.new('RGB', (4, 4), (25, 50, 75)).save(output, format='PNG')
    output.seek(0)
    with Image.open(output) as image:
        image.load()
        assert image.getpixel((0, 0)) == (25, 50, 75)
    assert 'CPUExecutionProvider' in onnxruntime.get_available_providers()
    model = cp_model.CpModel()
    decision = model.new_int_var(0, 1, 'native_abi_fixture')
    model.maximize(decision)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    assert solver.solve(model) == cp_model.OPTIMAL
    assert solver.value(decision) == 1
