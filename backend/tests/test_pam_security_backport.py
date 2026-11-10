"""Explicit Linux image check against the actual pam_userdb and Berkeley DB."""
import ctypes
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest


class Datum(ctypes.Structure):
    _fields_ = [('data', ctypes.c_void_p), ('size', ctypes.c_int)]


class PamMessage(ctypes.Structure):
    _fields_ = [('style', ctypes.c_int), ('message', ctypes.c_char_p)]


class PamResponse(ctypes.Structure):
    _fields_ = [('response', ctypes.c_void_p), ('return_code', ctypes.c_int)]


Conversation = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(PamMessage)),
    ctypes.POINTER(ctypes.POINTER(PamResponse)), ctypes.c_void_p,
)


class PamConversation(ctypes.Structure):
    _fields_ = [('callback', Conversation), ('data', ctypes.c_void_p)]


@unittest.skipUnless(
    platform.system() == 'Linux' and os.environ.get('TRIP_RUNTIME_PAM_CHECK') == '1',
    'Explicit PAM backport image check only',
)
class PamUserDBBackportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = Path(os.environ.get(
            'TRIP_PAM_USERDB_MODULE',
            '/usr/lib/x86_64-linux-gnu/security/pam_userdb.so',
        ))
        assert cls.module.is_absolute() and cls.module.is_file(), cls.module
        cls.db = ctypes.CDLL('libdb-5.3.so')
        cls.db_open = getattr(cls.db, '__db_ndbm_open')
        cls.db_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
        cls.db_open.restype = ctypes.c_void_p
        cls.db_store = getattr(cls.db, '__db_ndbm_store')
        cls.db_store.argtypes = [ctypes.c_void_p, Datum, Datum, ctypes.c_int]
        cls.db_store.restype = ctypes.c_int
        cls.db_close = getattr(cls.db, '__db_ndbm_close')
        cls.db_close.argtypes = [ctypes.c_void_p]
        cls.db_close.restype = None
        cls.pam = ctypes.CDLL('libpam.so.0')
        cls.pam.pam_start_confdir.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(PamConversation),
            ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p),
        ]
        cls.pam.pam_start_confdir.restype = ctypes.c_int
        cls.pam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
        cls.pam.pam_authenticate.restype = ctypes.c_int
        cls.pam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
        cls.pam.pam_end.restype = ctypes.c_int
        cls.libc = ctypes.CDLL(None)
        cls.libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
        cls.libc.calloc.restype = ctypes.c_void_p
        cls.libc.strdup.argtypes = [ctypes.c_char_p]
        cls.libc.strdup.restype = ctypes.c_void_p
        cls.libc.free.argtypes = [ctypes.c_void_p]
        cls.libc.free.restype = None
        cls.crypt = ctypes.CDLL('libcrypt.so.1')
        cls.crypt.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        cls.crypt.crypt.restype = ctypes.c_char_p

    def make_database(self, filename, entries):
        database = self.db_open(os.fsencode(filename), os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
        self.assertTrue(database, 'Berkeley DB creation failed')
        try:
            for key, value in entries.items():
                key_buffer = ctypes.create_string_buffer(key)
                value_buffer = ctypes.create_string_buffer(value)
                # The database payload excludes the buffers' trailing NULs.
                status = self.db_store(
                    database, Datum(ctypes.addressof(key_buffer), len(key)),
                    Datum(ctypes.addressof(value_buffer), len(value)), 0,
                )
                self.assertEqual(status, 0)
        finally:
            self.db_close(database)
        self.assertTrue(filename.with_suffix('.db').is_file())

    def authenticate(self, confdir, user, password):
        @Conversation
        def conversation(count, messages, responses, _data):
            # pam_get_user receives the supplied username; this module then
            # prompts once for its password via obtain_authtok.
            if count != 1 or messages[0].contents.style != 1:
                return 19  # PAM_CONV_ERR
            block = self.libc.calloc(1, ctypes.sizeof(PamResponse))
            if not block:
                return 5  # PAM_BUF_ERR
            response = ctypes.cast(block, ctypes.POINTER(PamResponse))
            response[0].response = self.libc.strdup(password)
            if not response[0].response:
                self.libc.free(block)
                return 5
            responses[0] = response
            return 0  # libpam takes ownership of the response allocation.

        handler = PamConversation(conversation, None)
        handle = ctypes.c_void_p()
        self.assertEqual(self.pam.pam_start_confdir(
            b'trip-userdb', user, ctypes.byref(handler), os.fsencode(confdir), ctypes.byref(handle),
        ), 0)
        try:
            return self.pam.pam_authenticate(handle, 0)
        finally:
            self.assertEqual(self.pam.pam_end(handle, 0), 0)

    def check_mode(self, *, key_only=False, icase=False, encrypted=False):
        password = b'SeCrEt-Pass!'
        value = self.crypt.crypt(password, b'$6$triptest$') if encrypted else password
        self.assertTrue(value)
        if encrypted:
            self.assertTrue(value.startswith(b'$6$'))
        with tempfile.TemporaryDirectory(prefix='trip-pam-') as directory:
            folder = Path(directory)
            filename = folder / 'accounts'
            entries = {b'alice-' + password: b'ignored', b'a': b'x'} if key_only else {
                b'alice': value, b'a': b'x',
            }
            self.make_database(filename, entries)
            options = ['crypt=crypt' if encrypted else 'crypt=none']
            if key_only:
                options.append('key_only')
            if icase:
                options.append('icase')
            (folder / 'trip-userdb').write_text(
                f'auth required {self.module} db={filename} {" ".join(options)}\n', encoding='utf-8',
            )
            cases = [
                ('exact', b'alice', password, 0),
                ('case_variant', b'alice', password.lower(), 0 if icase else 7),
                ('prefix', b'alice', b'SeCrEt-', 7),
                ('suffix', b'alice', password + b'suffix', 7),
                ('wrong_first', b'alice', b'XeCrEt-Pass!', 7),
                ('wrong_last', b'alice', b'SeCrEt-Pass?', 7),
                ('empty', b'alice', b'', 7),
                ('unknown_user', b'bob', password, 10),
                ('user_prefix', b'ali', password, 10),
                ('user_longer_than_short_db_key', b'a' * 80, password, 10),
            ]
            for name, user, supplied, expected in cases:
                with self.subTest(case=name, key_only=key_only, icase=icase, encrypted=encrypted):
                    self.assertEqual(self.authenticate(folder, user, supplied), expected)

    def test_plaintext(self):
        self.check_mode()

    def test_plaintext_case_insensitive(self):
        self.check_mode(icase=True)

    def test_key_only(self):
        self.check_mode(key_only=True)

    def test_key_only_case_insensitive(self):
        self.check_mode(key_only=True, icase=True)

    def test_encrypted_password_path_still_authenticates(self):
        self.check_mode(encrypted=True)

    def test_installed_backport_matches_reviewed_patch_and_module(self):
        self.assertEqual(subprocess.check_output([
            'dpkg-query', '-W', '-f=${Version}', 'libpam-modules',
        ], text=True), '1.7.0-5+trip1')
        provenance = Path('/usr/local/share/tripplanner/pam-backport')
        patch = provenance / 'CVE-2026-54411.patch'
        self.assertEqual(hashlib.sha256(patch.read_bytes()).hexdigest(),
                         '18867075bd5f2a01f0f1868bb23c5a9edecfef7306a382d373eb507683f4f621')
        digest, installed_path = (provenance / 'pam-userdb-sha256.txt').read_text().split()
        self.assertEqual(Path(installed_path), self.module)
        self.assertEqual(hashlib.sha256(self.module.read_bytes()).hexdigest(), digest)


if __name__ == '__main__':
    unittest.main(verbosity=2)
