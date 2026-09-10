import hashlib
import importlib.util
import json
from pathlib import Path


def test_backup_policy_rejects_corruption_without_deleting(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'scripts/check_backup_retention.py'
    spec = importlib.util.spec_from_file_location('backup_policy', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    folder = tmp_path / 'backup'
    folder.mkdir()
    hashes = {}
    for name in ('database.dump', 'application-data.tar.gz'):
        (folder / name).write_bytes(b'fixture')
        hashes[name] = hashlib.sha256(b'fixture').hexdigest()
    (folder / 'manifest.json').write_text(json.dumps({'sha256': hashes}))
    assert module.inspect(tmp_path)['backups'][0]['valid']
    (folder / 'database.dump').write_bytes(b'corrupt')
    report = module.inspect(tmp_path)
    assert not report['backups'][0]['valid']
    assert report['deleted'] == 0 and (folder / 'database.dump').exists()
