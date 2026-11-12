#!/usr/bin/env python3
"""Apply the exact upstream AAD regression bodies at OpenSSL 3.5.7 anchors."""
import hashlib
from pathlib import Path
import sys

PATCH_SHA256 = 'b02374041ba40333c3a7537486e193c9a70de4beedff5056ff3958a8abe074d4'
patch_path = Path(__file__).resolve().parent / 'patches/0006-CVE-2026-88823-test.patch'
source_path = Path(sys.argv[1])
patch_bytes = patch_path.read_bytes()
if hashlib.sha256(patch_bytes).hexdigest() != PATCH_SHA256:
    raise SystemExit('upstream AAD regression patch hash mismatch')
patch_lines = patch_bytes.decode('utf-8').splitlines()
hunk_starts = [i for i, line in enumerate(patch_lines) if line.startswith('@@ ')]
if len(hunk_starts) != 3:
    raise SystemExit('upstream AAD regression patch hunk count changed')

def additions(start, end):
    return [line[1:] for line in patch_lines[start:end]
            if line.startswith('+') and not line.startswith('+++')]

inserted_functions = additions(hunk_starts[1], hunk_starts[2])
registrations = additions(hunk_starts[2], len(patch_lines))
if len(inserted_functions) != 401 or len(registrations) != 5:
    raise SystemExit('upstream AAD regression assertions changed')
text = source_path.read_text(encoding='utf-8')
old_fetch = 'EVP_CIPHER *c = EVP_CIPHER_fetch(NULL, "AES-128-GCM-SIV", NULL);'
new_fetch = 'EVP_CIPHER *c = EVP_CIPHER_fetch(testctx, "AES-128-GCM-SIV", testpropq);'
if text.count(old_fetch) != 1 or 'test_aes_gcm_siv_zero_length_aad' in text:
    raise SystemExit('OpenSSL 3.5.7 test setup no longer matches the reviewed adaptation')
text = text.replace(old_fetch, new_fetch, 1)
anchor = 'int setup_tests(void)\n'
if text.count(anchor) != 1:
    raise SystemExit('OpenSSL 3.5.7 test registration anchor changed')
text = text.replace(anchor, '\n'.join(inserted_functions) + '\n\n' + anchor, 1)
registration_anchor = '    ADD_TEST(test_aes_gcm_siv_empty_data);\n'
if text.count(registration_anchor) != 1:
    raise SystemExit('OpenSSL 3.5.7 test registration list changed')
text = text.replace(registration_anchor, registration_anchor + '\n'.join(registrations) + '\n', 1)
source_path.write_text(text, encoding='utf-8', newline='\n')
print(f'Applied {len(inserted_functions)} upstream test lines and {len(registrations)} upstream test registrations to OpenSSL 3.5.7 anchors.')
