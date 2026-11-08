"""Pinned CLI downloads retry only timeouts and never cache partial/unverified bytes."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from tools import security_gate as gate


class ScannerDownloadTests(unittest.TestCase):
    payload=b'isolated-pinned-scanner-content'

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        pin={'version':'fixture','linux':{'url':'https://example.invalid/scanner','sha256':hashlib.sha256(self.payload).hexdigest()}}
        for target,value in [('ROOT',self.root),('PINS',{'fixture':pin})]:
            context=patch.object(gate,target,value);context.start();self.addCleanup(context.stop)
        context=patch.object(gate.platform,'system',return_value='Linux');context.start();self.addCleanup(context.stop)
        context=patch.object(gate.platform,'machine',return_value='x86_64');context.start();self.addCleanup(context.stop)
        self.archive=self.root/'.cache/security-tools/fixture/fixture/scanner'

    def test_read_timeout_retries_then_caches_only_the_verified_complete_asset(self):
        response=MagicMock();response.__enter__.return_value=response
        response.read.side_effect=TimeoutError('read timed out after response opened')
        with patch.object(gate,'urlopen',side_effect=[response,io.BytesIO(self.payload)]) as fetch:
            self.assertEqual(str(self.archive),gate.tool('fixture'))
        self.assertEqual(2,fetch.call_count)
        self.assertEqual(self.payload,self.archive.read_bytes())

    def test_three_timeouts_raise_and_do_not_cache_an_incomplete_asset(self):
        with patch.object(gate,'urlopen',side_effect=TimeoutError('read timed out')) as fetch:
            with self.assertRaises(TimeoutError):gate.tool('fixture')
        self.assertEqual(3,fetch.call_count)
        self.assertFalse(self.archive.exists())

    def test_wrapped_connection_timeout_can_retry_without_retrying_http_errors(self):
        with patch.object(gate,'urlopen',side_effect=[URLError(TimeoutError('connect timed out')),io.BytesIO(self.payload)]) as fetch:
            gate.tool('fixture')
        self.assertEqual(2,fetch.call_count)

    def test_http_error_is_not_retried_or_cached(self):
        with patch.object(gate,'urlopen',side_effect=HTTPError('https://example.invalid/scanner',404,'not found',{},None)) as fetch:
            with self.assertRaises(HTTPError):gate.tool('fixture')
        self.assertEqual(1,fetch.call_count)
        self.assertFalse(self.archive.exists())

    def test_checksum_mismatch_is_not_retried_or_cached(self):
        with patch.object(gate,'urlopen',return_value=io.BytesIO(b'wrong-content')) as fetch:
            with self.assertRaisesRegex(RuntimeError,'SHA256'):gate.tool('fixture')
        self.assertEqual(1,fetch.call_count)
        self.assertFalse(self.archive.exists())


if __name__=='__main__':unittest.main()
