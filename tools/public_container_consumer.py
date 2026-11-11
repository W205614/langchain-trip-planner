"""Anonymous release/source/image acquisition; no provider calls or deployment."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

from container_release import CONFIG_FIELDS

REPOSITORY = 'W205614/langchain-trip-planner'
SERVICES = ('agent', 'backend', 'frontend')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def request(url):
    # Deliberately no Authorization header or credential environment lookup.
    try:
        return urlopen(Request(url, headers={'User-Agent': 'trip-anonymous-consumer'}), timeout=120)
    except (HTTPError, URLError) as error:
        parsed = urlsplit(url)
        raise RuntimeError(f'Anonymous HTTP acquisition failed: {parsed.netloc}{parsed.path} ({type(error).__name__})') from None


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checksum_entries(content):
    result = {}
    for line in content.decode('utf-8').splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  (.+)', line)
        require(bool(match), 'Malformed checksum entry')
        name = match[2]
        path = PurePosixPath(name)
        require(not path.is_absolute() and '..' not in path.parts and '\\' not in name
                and name not in result, 'Unsafe/duplicate checksum path: ' + name)
        result[name] = match[1]
    return result


def verify_source_bundle(path, manifest, project_archive):
    with zipfile.ZipFile(path) as archive, zipfile.ZipFile(io.BytesIO(project_archive)) as tag:
        names = archive.namelist()
        require(len(names) == len(set(names)), 'Duplicate source ZIP member')
        require(archive.testzip() is None, 'Source ZIP CRC failed')
        sums = checksum_entries(archive.read('SHA256SUMS'))
        require(set(names) == set(sums) | {'SHA256SUMS'}, 'Source ZIP and inner checksum members differ')
        for name, expected in sums.items():
            with archive.open(name) as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == expected, 'Source member hash differs: ' + name)
        source = json.loads(archive.read('manifest.json'))
        for service in SERVICES:
            require(source['images'][service]['id'] == manifest['images'][service]['base_id']
                    and source['images'][service]['layers'] == manifest['images'][service]['base_layers'],
                    'Source material describes a different application base: ' + service)
        for name, row in source['downloads'].items():
            require(sums.get(name) == row['sha256'], 'Source download record differs from bundled bytes: ' + name)
        project = [name for name in names if name.startswith('project/')]
        require(bool(project), 'Source bundle contains no project rebuild materials')
        for name in project:
            relative = name.removeprefix('project/')
            require(relative in tag.namelist() and archive.read(name) == tag.read(relative),
                    'Bundled project source differs from the canonical Tag: ' + relative)
        return {'members': len(names), 'source_downloads': len(source['downloads']),
                'all_inner_hashes_verified': True, 'zip_crc_passed': True, 'project_files_match_tag': True}


def private_source_path(name):
    return (name.startswith(('.git/', '.codex-', 'backups/', 'deploy/runtime/',
                             'backend/data/chroma/', 'backend/data/knowledge_uploads/', 'backend/data/agent-runtime/'))
            or name in ('AGENTS.md', 'backend/.env', 'frontend/.env', '.env')
            or 'node_modules' in PurePosixPath(name).parts)


def compare_tag_archive(raw, local):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), 'Duplicate hosted source archive member')
        require(archive.testzip() is None, 'Hosted source archive CRC failed')
        roots = {name.split('/')[0] for name in names}
        require(len(roots) == 1, 'Hosted source archive has multiple roots')
        require(all('/' in name and not name.startswith('/') and '..' not in PurePosixPath(name).parts
                    and '\\' not in name for name in names), 'Unsafe hosted source archive path')
        files = {name.split('/', 1)[1]: archive.read(name) for name in names if not name.endswith('/')}
    with zipfile.ZipFile(io.BytesIO(local)) as archive:
        expected = {name: archive.read(name) for name in archive.namelist() if not name.endswith('/')}
    require(files == expected, 'Hosted source archive differs from canonical git archive bytes')
    require(not any(private_source_path(name) for name in files), 'Private/runtime path present in hosted source')
    require({'LICENSE', 'THIRD_PARTY_NOTICES.md'} <= files.keys(), 'Project source license/notice missing')
    return {'source_files': len(files), 'all_file_contents_match_commit': True,
            'private_paths_present': False, 'archive_root': next(iter(roots)),
            'comparison_uses_canonical_git_line_endings': True}


def registry_image_checks(actual, registry, expected, source, platform):
    require(registry['config']['digest'] == expected['config_digest'], 'Registry configuration digest differs from candidate')
    require(actual['RootFS']['Layers'] == expected['layers'], 'Pulled ordered layers differ from candidate')
    require({key: actual['Config'].get(key) or None for key in CONFIG_FIELDS} == expected['configuration'],
            'Pulled operational configuration differs from candidate')
    require(actual['Os'] + '/' + actual['Architecture'] == platform, 'Pulled image platform differs')
    require(actual['Config']['Labels']['org.opencontainers.image.revision'] == source, 'Pulled image source differs')
    # Docker/containerd inspect.Id need not equal the OCI archive config digest.


def consume(args):
    args.output.mkdir(exist_ok=False)
    args.downloads.mkdir(exist_ok=False)
    evidence = {'passed': False, 'version': args.version, 'source': args.source,
                'boundary': 'Anonymous artifact/image acquisition and three network-none command probes; not full-stack acceptance, provider verification or deployment.'}
    try:
        expected_names = {f'application-images-{args.version}.tar.gz', f'container-sources-{args.version}.zip',
                          'container-manifest.json', 'SHA256SUMS'}
        with request(f'https://api.github.com/repos/{REPOSITORY}/releases/tags/{args.version}') as response:
            release = json.load(response)
        require(not release['draft'] and not release['prerelease'] and release['target_commitish'] == args.source,
                'Expected a public stable Release bound to this source')
        assets = release['assets']
        require(len(assets) == 4 and {asset['name'] for asset in assets} == expected_names, 'Release must contain exactly four intended assets')
        hashes = {}
        for asset in assets:
            name = asset['name']
            require(bool(re.fullmatch(r'sha256:[a-f0-9]{64}', asset.get('digest') or '')), 'Release asset lacks SHA256: ' + name)
            target = args.downloads / name
            with request(asset['browser_download_url']) as response, target.open('xb') as stream:
                while block := response.read(1024 * 1024):
                    stream.write(block)
            require(target.stat().st_size == asset['size'] and 'sha256:' + sha(target) == asset['digest'], 'Full anonymous asset hash differs: ' + name)
            hashes[name] = asset['digest'].removeprefix('sha256:')
            print('Anonymous full asset hash verified: ' + name, flush=True)
        outer = checksum_entries((args.downloads / 'SHA256SUMS').read_bytes())
        require(set(outer) == expected_names - {'SHA256SUMS'} and all(hashes[name] == value for name, value in outer.items()),
                'Outer SHA256SUMS differs from public assets')
        manifest = json.loads((args.downloads / 'container-manifest.json').read_bytes())
        require(manifest['source'] == args.source and manifest['version'] == args.version
                and set(manifest['images']) == set(SERVICES), 'Wrong candidate manifest/source/version')
        require(manifest['archive']['name'] == f'application-images-{args.version}.tar.gz'
                and manifest['archive']['sha256'] == hashes[manifest['archive']['name']], 'Image archive manifest mismatch')
        tag_object = subprocess.check_output(['git', 'rev-parse', 'refs/tags/' + args.version], text=True).strip()
        require(subprocess.check_output(['git', 'rev-parse', args.version + '^{}'], text=True).strip() == args.source,
                'Checked-out tag does not dereference to expected GITHUB_SHA')
        with request(f'https://api.github.com/repos/{REPOSITORY}/zipball/refs/tags/{args.version}') as response:
            raw = response.read()
        local = subprocess.check_output(['git', '-c', 'core.autocrlf=false', 'archive', '--format=zip', args.source])
        source_result = compare_tag_archive(raw, local)
        evidence['source_material'] = verify_source_bundle(args.downloads / f'container-sources-{args.version}.zip', manifest, local)
        source_result.update(passed=True, tag=args.version, commit=args.source, tag_object=tag_object,
                             zip_bytes=len(raw), zip_sha256=hashlib.sha256(raw).hexdigest())
        (args.output / 'source-archive-verification.json').write_text(json.dumps(source_result, indent=2) + '\n', encoding='utf-8')
        config = args.output / 'empty-docker-config'
        config.mkdir()
        (config / 'config.json').write_text('{"auths":{}}\n', encoding='ascii')
        prefix = ['docker', '--config', str(config.resolve())]
        images = {}
        for service in SERVICES:
            expected = manifest['images'][service]
            repository = f'ghcr.io/w205614/langchain-trip-planner-{service}'
            tag = repository + ':' + args.version
            require(expected['tag'] == tag, 'Unexpected application registry target')
            subprocess.run(prefix + ['pull', tag], check=True)
            actual = json.loads(subprocess.check_output(prefix + ['image', 'inspect', tag]))[0]
            references = [value for value in actual['RepoDigests'] if value.startswith(repository + '@sha256:')]
            require(len(references) == 1, 'Ambiguous pulled registry digest: ' + service)
            reference = references[0]
            registry = json.loads(subprocess.check_output(prefix + ['buildx', 'imagetools', 'inspect', reference, '--raw']))
            registry_image_checks(actual, registry, expected, args.source, manifest['platform'])
            if service == 'agent':
                command = ['python', '-c', "from pathlib import Path;print(Path('/licenses/SOURCE-ACCESS.txt').read_text());import app.agent_api.main;print('Agent import OK')"]
            elif service == 'backend':
                command = ['java', '-version']
            else:
                command = ['/docker-entrypoint.sh', 'nginx', '-t']
            subprocess.run(prefix + ['run', '--rm', '--network', 'none', '--add-host', 'backend:127.0.0.1',
                                     '--entrypoint', command[0], reference, *command[1:]], check=True)
            images[service] = {'reference': reference, 'image_id': actual['Id'], 'anonymous_pull': True,
                               'layers_and_configuration_match': True, 'registry_config_digest_verified': True,
                               'runtime_command_passed': True}
        evidence.update(passed=True, assets=hashes, images=images, anonymous_release_assets_downloaded=True,
                        all_outer_and_inner_hashes_verified=True, no_account_credentials_used=True,
                        asset_http_authorization_header=False, docker_config_empty=True)
    except Exception as error:
        evidence['failure'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        (args.output / 'public-acquisition.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--source', default=os.environ.get('GITHUB_SHA'))
    parser.add_argument('--output', type=Path, default=Path('consumer-evidence'))
    parser.add_argument('--downloads', type=Path, default=Path('consumer-downloads'))
    args = parser.parse_args()
    require(bool(re.fullmatch(r'v\d+\.\d+\.\d+', args.version)), 'Version must be vX.Y.Z')
    require(bool(re.fullmatch(r'[a-f0-9]{40}', args.source or '')), 'Expected full GITHUB_SHA/source commit')
    consume(args)


if __name__ == '__main__':
    main()
