"""Lower OCI layers retain distributable bytes after runtime removal/upgrade."""
import hashlib
import importlib.util
import io
import json
import lzma
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('historical_container_sources', Path(__file__).resolve().parents[1] / 'container_sources.py')
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)


def tar_bytes(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


class HistoricalAlpineSourcesTests(unittest.TestCase):
    def make_archive(self, directory, *, corrupt_layer=False, wrong_image=False, debian=False):
        first = b'P:old-copyleft\nV:1-r0\no:original\nc:' + b'a' * 40 + b'\nL:GPL-3.0-or-later\n'
        final = b'P:current\nV:2-r0\no:current\nc:' + b'b' * 40 + b'\nL:MIT\n'
        database = 'lib/apk/db/installed'
        if debian:
            database = 'var/lib/dpkg/status'
            first = b'Package: library\nStatus: install ok installed\nVersion: 1:1.2-1+deb13u3\nSource: upstream (1:1.2-1+deb13u3)\n'
            final = b'Package: library\nStatus: install ok installed\nVersion: 1:1.2-1+deb13u4+trip2\nSource: upstream (1:1.2-1+deb13u4+trip2)\n'
        layers = [tar_bytes({database: first, 'usr/bin/removed': b'original executable'}),
                  tar_bytes({database: final, 'usr/bin/.wh.removed': b''})]
        identities = ['sha256:' + hashlib.sha256(layer).hexdigest() for layer in layers]
        image = {'Id': 'immutable-image', 'RootFS': {'Layers': identities}, 'Config': {'Env': []}}
        files = {f'{index}/layer.tar': layer for index, layer in enumerate(layers)}
        if corrupt_layer:
            files['0/layer.tar'] = tar_bytes({'lib/apk/db/installed': first.replace(b'old-copyleft', b'tampered')})
        config_ids = ['sha256:wrong'] if wrong_image else identities
        files['config.json'] = json.dumps({'rootfs': {'diff_ids': config_ids}, 'config': image['Config']}).encode()
        files['manifest.json'] = json.dumps([{'Config': 'config.json', 'Layers': ['0/layer.tar', '1/layer.tar'], 'RepoTags': []}]).encode()
        path = directory / 'image.tar.gz'
        path.write_bytes(tar_bytes(files))
        return path, image

    def test_collects_removed_original_package_and_binds_all_layer_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, image = self.make_archive(root)
            with patch.object(sources, 'alpine_recipe_sources', return_value={}) as collect:
                result = sources.historical_alpine_sources(sources.Collector(root / 'sources'), archive, {'backend': image})
            self.assertEqual({call.args[1:3] for call in collect.call_args_list}, {('original', 'a' * 40), ('current', 'b' * 40)})
            row = result['images']['backend']
            self.assertEqual(len(row['databases']), 2)
            self.assertEqual({p['P'] for p in row['packages']}, {'old-copyleft', 'current'})
            self.assertTrue((root / 'sources' / row['databases'][0]['path']).is_file())

    def test_wrong_candidate_or_corrupted_layer_reject_before_source_fetch(self):
        for option, message in (('wrong_image', 'actual image'), ('corrupt_layer', 'diff-ID')):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive, image = self.make_archive(root, **{option: True})
                with patch.object(sources, 'alpine_recipe_sources') as collect:
                    with self.assertRaisesRegex(RuntimeError, message):
                        sources.historical_alpine_sources(sources.Collector(root / 'sources'), archive, {'backend': image})
                collect.assert_not_called()

    def test_debian_history_keeps_distinct_updates_epochs_and_downstream_patch_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, image = self.make_archive(root, debian=True)
            result = sources.historical_debian_packages(sources.Collector(root / 'sources'), archive, image)
            self.assertEqual({p['source_version'] for p in result['packages']},
                             {'1:1.2-1+deb13u3', '1:1.2-1+deb13u4+trip2'})
            self.assertEqual({p['source'] for p in result['packages']}, {'upstream'})
            self.assertEqual(len(result['databases']), 2)


class HistoricalDebianSourceDownloadTests(unittest.TestCase):
    def check_shared_archive(self, mismatched_checksum=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'upstream_1.0.orig.tar.gz'
            source.write_bytes(b'exact shared upstream source')
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            second = '0' * 64 if mismatched_checksum else digest
            index = root / 'Sources.xz'
            index.write_bytes(lzma.compress(('\n\n'.join(
                'Package: upstream\nVersion: ' + version + '\nDirectory: pool/main/u/upstream\n'
                'Checksums-Sha256:\n ' + value + ' ' + str(source.stat().st_size) + ' ' + source.name
                for version, value in [('1.0-1', digest), ('1.0-2', second)]) + '\n').encode()))
            collector = sources.Collector(root)
            fetches = []

            def fetch(url, relative, *args):
                fetches.append(relative)
                return index if relative.endswith('Sources.xz') else source

            with patch.object(sources, 'docker', return_value=b'library\t1.0-1\tupstream\t1.0-1\n'), \
                    patch.object(collector, 'fetch', side_effect=fetch):
                arguments = (collector, 'exact-image', 'debian', [('deb.debian.org', 'trixie')])
                additional = [{'package': 'library', 'version': '1.0-2', 'source': 'upstream', 'source_version': '1.0-2'}]
                if mismatched_checksum:
                    with self.assertRaisesRegex(RuntimeError, 'Conflicting source checksum'):
                        sources.apt_sources(*arguments, additional_packages=additional)
                else:
                    sources.apt_sources(*arguments, additional_packages=additional)
            self.assertEqual(fetches.count('debian/' + source.name), 1)

    def test_distinct_debian_revisions_share_one_writer_for_the_same_orig_archive(self):
        self.check_shared_archive()

    def test_shared_archive_conflicting_revision_checksum_is_rejected(self):
        self.check_shared_archive(mismatched_checksum=True)
