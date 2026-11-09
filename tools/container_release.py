"""Seal local application images, then promote the same archive from Actions.

Publication is manual, requires both exact-commit CI workflows, and never
rebuilds images or overwrites a version tag.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from container_sources import checksum

REPOSITORY = 'W205614/langchain-trip-planner'
SERVICES = ('agent', 'backend', 'frontend')
CONFIG_FIELDS = ('User', 'WorkingDir', 'Entrypoint', 'Cmd', 'Env', 'Labels', 'ExposedPorts', 'Volumes', 'Healthcheck', 'StopSignal', 'Shell')


def inspect(image):
    return json.loads(subprocess.check_output(['docker', 'image', 'inspect', image]))[0]


def api(path):
    request = Request('https://api.github.com/' + path, headers={
        'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'User-Agent': 'trip-container-release',
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    with urlopen(request, timeout=40) as response:
        return json.load(response)


def seal(args):
    sources = json.loads((args.sources / 'manifest.json').read_bytes())
    root = Path(__file__).resolve().parents[1]
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root).strip():
        raise RuntimeError('Seal requires a clean committed source')
    for path in (args.sources / 'project').rglob('*'):
        if path.is_file():
            relative = path.relative_to(args.sources / 'project').as_posix()
            committed = subprocess.check_output(['git', 'show', 'HEAD:' + relative], cwd=root)
            if path.read_bytes().replace(b'\r\n', b'\n') != committed.replace(b'\r\n', b'\n'):
                raise RuntimeError('Source material differs from the release commit: ' + relative)
    args.output.mkdir(parents=True, exist_ok=False)
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    manifest = {'version': args.version, 'source': source, 'platform': 'linux/amd64', 'images': {}}
    licenses = args.output / 'licenses'
    shutil.copytree(args.sources / 'licenses', licenses)
    shutil.copytree(args.sources / 'project/docs/licenses', licenses / 'project')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copyfile(root / name, licenses / name)
    (licenses / 'SOURCE-ACCESS.txt').write_text(
        f'Corresponding source and rebuild materials: https://github.com/{REPOSITORY}/releases/tag/{args.version}\n'
        f'Asset: container-sources-{args.version}.zip (verify SHA256SUMS).\n'
        'Third-party licenses and copyright notices retain their original terms.\n'
        'Shared LGPL libraries may be replaced or rebuilt; no additional restriction on debugging such modifications is imposed.\n', encoding='utf-8')
    tags = []
    for service in SERVICES:
        base = getattr(args, service)
        metadata = inspect(base)
        if (metadata['Os'], metadata['Architecture']) != ('linux', 'amd64'):
            raise RuntimeError('This distribution supports Linux/amd64 only')
        if metadata['RootFS']['Layers'] != sources['images'][service]['layers']:
            raise RuntimeError('Sources describe a different base image: ' + service)
        tag = 'ghcr.io/w205614/langchain-trip-planner-' + service + ':' + args.version
        dockerfile = args.output / (service + '.Dockerfile')
        dockerfile.write_text(
            f'FROM {base}\nCOPY licenses /licenses\n'
            f'LABEL org.opencontainers.image.source="https://github.com/{REPOSITORY}" '
            f'org.opencontainers.image.revision="{source}" org.opencontainers.image.version="{args.version}" '
            f'io.tripplanner.application.revision="{metadata["Config"]["Labels"].get("org.opencontainers.image.revision", "unknown")}"\n', encoding='utf-8')
        subprocess.run(['docker', 'build', '--network', 'none', '--provenance=false', '-f', str(dockerfile), '-t', tag, str(args.output)], check=True)
        final = inspect(tag)
        if final['RootFS']['Layers'][:-1] != metadata['RootFS']['Layers']:
            raise RuntimeError('Distribution wrapper changed runtime layers: ' + service)
        manifest['images'][service] = {'tag': tag, 'base_id': metadata['Id'], 'base_layers': metadata['RootFS']['Layers'],
            'application_revision': metadata['Config']['Labels'].get('org.opencontainers.image.revision'), 'layers': final['RootFS']['Layers']}
        tags.append(tag)
    archive = args.output / ('application-images-' + args.version + '.tar.gz')
    with archive.open('xb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as compressed:
            process = subprocess.Popen(['docker', 'image', 'save', *tags], stdout=subprocess.PIPE)
            shutil.copyfileobj(process.stdout, compressed)
            if process.wait() != 0:
                raise RuntimeError('Image export failed')
    with tarfile.open(archive) as bundle:
        entries = json.load(bundle.extractfile('manifest.json'))
        for service, image in manifest['images'].items():
            entry = next(e for e in entries if image['tag'] in e['RepoTags'])
            raw = bundle.extractfile(entry['Config']).read()
            image['config_digest'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
            configuration = json.loads(raw)['config']
            image['configuration'] = {field: configuration.get(field) or None for field in CONFIG_FIELDS}
    manifest['archive'] = {'name': archive.name, 'sha256': checksum(archive)}
    (args.output / 'container-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'source': source, 'version': args.version, 'images': tags, 'archive_bytes': archive.stat().st_size}))


def promote(args):
    if os.getenv('CI') != 'true' or os.getenv('GITHUB_REPOSITORY') != REPOSITORY or not os.getenv('GH_TOKEN'):
        raise RuntimeError('Promotion requires this repository Actions workflow')
    manifest = json.loads((args.directory / 'container-manifest.json').read_bytes())
    source = os.environ['GITHUB_SHA']
    if manifest['source'] != source or manifest['version'] != args.version or set(manifest['images']) != set(SERVICES):
        raise RuntimeError('Candidate version/source mismatch')
    if manifest['archive']['name'] != 'application-images-' + args.version + '.tar.gz':
        raise RuntimeError('Unexpected image archive name')
    runs = api(f'repos/{REPOSITORY}/actions/runs?head_sha={source}&event=push&per_page=100')['workflow_runs']
    for workflow in ('.github/workflows/ci.yml', '.github/workflows/security.yml'):
        run = next(r for r in runs if r['path'] == workflow)
        if run['status'] != 'completed' or run['conclusion'] != 'success':
            raise RuntimeError('Required exact-commit workflow did not pass: ' + workflow)
    release = api(f'repos/{REPOSITORY}/releases/tags/{args.version}')
    if release['draft'] or release['target_commitish'] != source or release['prerelease']:
        raise RuntimeError('Expected published stable Release with matching source materials')
    reference = api(f'repos/{REPOSITORY}/git/ref/tags/{args.version}')['object']
    if reference['type'] == 'tag':
        reference = api(f'repos/{REPOSITORY}/git/tags/{reference["sha"]}')['object']
    if reference['type'] != 'commit' or reference['sha'] != source:
        raise RuntimeError('Version tag differs from candidate source')
    sums = {}
    for line in (args.directory / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        name = name.lstrip('*')
        if Path(name).name != name or name in sums or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise RuntimeError('Invalid checksum entry')
        sums[name] = digest
    required = (manifest['archive']['name'], 'container-manifest.json', 'container-sources-' + args.version + '.zip')
    for name in required:
        asset = next(a for a in release['assets'] if a['name'] == name)
        digest = checksum(args.directory / name)
        if sums[name] != digest or asset.get('digest') != 'sha256:' + digest:
            raise RuntimeError('Release asset checksum mismatch: ' + name)
    if sums[manifest['archive']['name']] != manifest['archive']['sha256']:
        raise RuntimeError('Candidate archive mismatch')
    # Check every destination before pushing the first image.
    for service in SERVICES:
        name = 'langchain-trip-planner-' + service
        page = 1
        while True:
            try:
                versions = api(f'users/W205614/packages/container/{name}/versions?per_page=100&page={page}')
            except HTTPError as error:
                if error.code == 404 and page == 1:
                    break
                raise
            if any(args.version in v['metadata']['container']['tags'] for v in versions):
                raise RuntimeError('A published version already exists: ' + name)
            if len(versions) < 100:
                break
            page += 1
    subprocess.run(['docker', 'load', '-i', str(args.directory / manifest['archive']['name'])], check=True)
    for service, image in manifest['images'].items():
        expected = f'ghcr.io/w205614/langchain-trip-planner-{service}:{args.version}'
        actual = inspect(expected)
        configuration = {field: actual['Config'].get(field) or None for field in CONFIG_FIELDS}
        if image['tag'] != expected or actual['RootFS']['Layers'] != image['layers'] or configuration != image['configuration']:
            raise RuntimeError('Loaded image differs from the validated archive: ' + service)
        if actual['Config']['Labels']['org.opencontainers.image.revision'] != source:
            raise RuntimeError('Image source label differs from candidate')
    subprocess.run(['docker', 'login', 'ghcr.io', '-u', os.environ['GITHUB_ACTOR'], '--password-stdin'], input=os.environ['GH_TOKEN'].encode(), check=True)
    receipts = {'source': source, 'version': args.version, 'images': {}}
    try:
        for service, image in manifest['images'].items():
            subprocess.run(['docker', 'push', image['tag']], check=True)
            raw = subprocess.check_output(['docker', 'buildx', 'imagetools', 'inspect', image['tag'], '--raw'])
            registry = json.loads(raw)
            if registry['config']['digest'] != image['config_digest']:
                raise RuntimeError('Registry config differs from validated image')
            subprocess.run(['docker', 'pull', image['tag']], check=True)
            pulled = inspect(image['tag'])
            repository = image['tag'].rsplit(':', 1)[0]
            reference, = [r for r in pulled['RepoDigests'] if r.startswith(repository + '@sha256:')]
            configuration = {field: pulled['Config'].get(field) or None for field in CONFIG_FIELDS}
            if pulled['RootFS']['Layers'] != image['layers'] or configuration != image['configuration']:
                raise RuntimeError('Pulled image differs from validated image')
            receipts['images'][service] = {'tag': image['tag'], 'digest': reference.split('@', 1)[1], 'config_digest': image['config_digest']}
    finally:
        (args.directory / 'publication.json').write_text(json.dumps(receipts, indent=2) + '\n', encoding='utf-8')
        subprocess.run(['docker', 'logout', 'ghcr.io'], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    local = sub.add_parser('seal')
    for service in SERVICES:
        local.add_argument('--' + service, required=True)
    local.add_argument('--sources', type=Path, required=True)
    local.add_argument('--output', type=Path, required=True)
    publish = sub.add_parser('promote')
    publish.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'v\d+\.\d+\.\d+', args.version):
        parser.error('version must be vX.Y.Z')
    {'seal': seal, 'promote': promote}[args.command](args)


if __name__ == '__main__':
    main()
