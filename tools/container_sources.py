"""Collect sources and notices for the exact Linux/amd64 application images.

Uses distribution source indexes, installed package metadata and locked npm
archives. Missing identities or checksums stop collection before publication.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import gzip
from http.client import IncompleteRead
import io
import json
import lzma
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

# These PyPI releases have no sdist. Official release tags were resolved to
# immutable source commits on 2026-11-09; a different version requires review.
PYTHON_SOURCE_RELEASES = {
    ('flatbuffers', '25.12.19'): ('google/flatbuffers', 'v25.12.19', '7e163021e59cca4f8e1e35a7c828b5c6b7915953'),
    ('onnxruntime', '1.29.0'): ('microsoft/onnxruntime', 'v1.29.0', '2e2543fbe9fae542f921d47a72d21d5a4ef0b710'),
    ('ortools', '9.15.6755'): ('google/or-tools', 'v9.15', '551ad10d94835c99e5e1e684500d3db398c0e345'),
}


def run(*args):
    return subprocess.check_output(args)


def docker(image, *args):
    return run('docker', 'run', '--rm', '--network', 'none', '--entrypoint', args[0], image, *args[1:])


def paragraphs(text):
    for block in text.strip().split('\n\n'):
        fields, key = {}, None
        for line in block.splitlines():
            if line.startswith(' '):
                fields[key] += '\n' + line.strip()
            elif ':' in line:
                key, value = line.split(':', 1)
                fields[key] = value.strip()
        yield fields


def checksum(path, algorithm='sha256'):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


class Collector:
    def __init__(self, root):
        self.root = root
        self.downloads = {}

    def fetch(self, url, relative, expected=None, algorithm='sha256', *, immutable=False):
        relative = relative.replace(':', '%3A')
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('Source output path escapes the bundle')
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            print('Downloading ' + relative, flush=True)
            temporary = path.with_name(path.name + '.part')
            # Observed truncated HTTPS bodies must not become cached sources.
            # Resume checksum-bound sources or pinned Git archives. A complete
            # gzip stream is required for the latter before promotion.
            deadline = time.monotonic() + 600
            for attempt in range(12):
                try:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Source transfer exceeded ten minutes')
                    offset = temporary.stat().st_size if (expected or immutable) and temporary.exists() else 0
                    headers = {'User-Agent': 'trip-container-sources'}
                    if offset:
                        headers['Range'] = f'bytes={offset}-'
                    with urlopen(Request(url, headers=headers), timeout=min(120, deadline-time.monotonic())) as response:
                        if response.status == 206:
                            content_range = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                            if not content_range or int(content_range[1]) != offset:
                                raise RuntimeError('Source range does not match the requested offset')
                        else:
                            offset = 0
                        length = response.headers.get('Content-Length')
                        with temporary.open('ab' if offset else 'wb') as output:
                            while chunk := response.read(64 * 1024):
                                output.write(chunk)
                                if time.monotonic() >= deadline:
                                    raise TimeoutError('Source transfer exceeded ten minutes')
                        if length and temporary.stat().st_size != offset + int(length):
                            raise IncompleteRead(b'', offset + int(length) - temporary.stat().st_size)
                    if not expected and relative.endswith(('.tar.gz', '.tgz')):
                        with gzip.open(temporary) as archive:
                            while archive.read(1024 * 1024):
                                pass
                    break
                except HTTPError as error:
                    raise RuntimeError(f'Source HTTP {error.code}: {url}') from error
                except (IncompleteRead, EOFError, URLError, TimeoutError, ConnectionError):
                    if attempt == 11 or time.monotonic() >= deadline:
                        raise
                    print('Retrying truncated/failed transport: ' + relative, flush=True)
            if expected and checksum(temporary, algorithm) != expected:
                raise RuntimeError('Source checksum mismatch: ' + relative)
            temporary.replace(path)
        if expected and checksum(path, algorithm) != expected:
            raise RuntimeError('Source checksum mismatch: ' + relative)
        if not expected and relative.endswith(('.tar.gz', '.tgz')):
            with gzip.open(path) as archive:
                while archive.read(1024 * 1024):
                    pass
        self.downloads[relative] = {'url': url, 'sha256': checksum(path), 'bytes': path.stat().st_size,
                                    'upstream_checksum': expected, 'algorithm': algorithm if expected else None, 'immutable_reference': immutable}
        return path

    def json(self, url, relative):
        return json.loads(self.fetch(url, relative).read_bytes())


def apt_sources(collector, image, distribution, suites):
    installed = docker(image, 'dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n').decode()
    packages = [dict(zip(('package', 'version', 'source', 'source_version'), row.split('\t'))) for row in installed.splitlines()]
    needed = {(p['source'], re.sub(r'\+trip\d+$', '', p['source_version'])) for p in packages}
    found = {}
    for host, suite in suites:
        archive = 'debian-security' if host == 'security.debian.org' else distribution
        url = f'https://{host}/{archive}/dists/{suite}/main/source/Sources.xz'
        path = collector.fetch(url, f'indexes/{distribution}-{suite}-Sources.xz')
        for entry in paragraphs(lzma.decompress(path.read_bytes()).decode()):
            identity = entry.get('Package'), entry.get('Version')
            if identity in needed:
                found[identity] = host, entry
    tasks = []
    for identity, (host, entry) in found.items():
        archive = 'debian-security' if host == 'security.debian.org' else distribution
        for row in entry['Checksums-Sha256'].splitlines():
            if row.strip():
                digest, _, filename = row.split()
                tasks.append((f'https://{host}/{archive}/{entry["Directory"]}/{filename}', f'{distribution}/{filename}', digest))
    for name, version in sorted(needed - found.keys()):
        if distribution == 'ubuntu':
            url = 'https://api.launchpad.net/1.0/ubuntu/+archive/primary?ws.op=getPublishedSources&exact_match=true'
            records = collector.json(url + f'&source_name={quote(name)}&version={quote(version)}', f'indexes/ubuntu-{name}.json')
            publication = next(r for r in records['entries'] if r['source_package_version'] == version)
            urls = collector.json(publication['self_link'] + '?ws.op=sourceFileUrls', f'indexes/ubuntu-{name}-files.json')
            dsc_url = next(u for u in urls if u.endswith('.dsc'))
            dsc = collector.fetch(dsc_url, 'ubuntu/' + dsc_url.rsplit('/', 1)[1]).read_text()
            sums = re.search(r'Checksums-Sha256:\n((?: .+\n)+)', dsc).group(1)
            for row in sums.splitlines():
                digest, _, filename = row.split()
                tasks.append((next(u for u in urls if u.endswith('/' + filename)), 'ubuntu/' + filename, digest))
        else:
            records = collector.json(f'https://snapshot.debian.org/mr/package/{quote(name)}/{quote(version)}/srcfiles', f'indexes/debian-{name}-{version}.json')
            for record in records['result']:
                digest = record['hash']
                details = collector.json(f'https://snapshot.debian.org/mr/file/{digest}/info', f'indexes/debian-file-{digest}.json')
                filename = details['result'][0]['name']
                tasks.append((f'https://snapshot.debian.org/file/{digest}', 'debian/' + filename, digest, 'sha1'))
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda task: collector.fetch(*task), tasks))
    return packages


def alpine_sources(collector, image):
    packages = list(paragraphs(docker(image, 'cat', '/lib/apk/db/installed').decode()))
    packages = [{k: row.get(k) for k in ('P', 'V', 'L', 'o', 'c')} for row in packages]
    origins = {(row['o'], row['c']) for row in packages if row['c']}
    for origin, commit in sorted(origins):
        raw = collector.fetch(f'https://gitlab.alpinelinux.org/alpine/aports/-/archive/{commit}/aports-{commit}.tar.gz?path=main/{origin}', f'alpine/recipes/{origin}-{commit}.tar.gz', immutable=True)
        with tarfile.open(raw) as archive:
            files = {Path(member.name).name: archive.extractfile(member).read() for member in archive.getmembers() if member.isfile()}
        recipe = files['APKBUILD'].decode()
        sums = re.search(r'sha512sums="(.*?)"', recipe, re.S)
        if not sums:
            if re.search(r'^source=', recipe, re.M):
                raise RuntimeError('Missing Alpine checksums: ' + origin)
            continue
        for digest, filename in re.findall(r'([a-f0-9]{128})\s+(\S+)', sums.group(1)):
            if filename in files:
                if hashlib.sha512(files[filename]).hexdigest() != digest:
                    raise RuntimeError('Alpine recipe checksum mismatch: ' + filename)
            else:
                url = 'https://distfiles.alpinelinux.org/distfiles/v3.24/' + filename
                relative = 'alpine/distfiles/' + filename
                if origin == 'libyuv' and filename.startswith('libyuv-'):
                    # Its official fetch/unpack overrides validate sorted source
                    # contents because Gitiles changes archive timestamps.
                    expected = re.search(r'^_libyuvchecksum=([a-f0-9]{128})$', recipe, re.M).group(1)
                    path = collector.fetch(url, relative)
                    content_hash = hashlib.sha512()
                    with tarfile.open(path) as source:
                        for member in sorted(source.getmembers(), key=lambda item: item.name):
                            if member.isfile():
                                content_hash.update(source.extractfile(member).read())
                    if content_hash.hexdigest() != expected:
                        raise RuntimeError('Alpine libyuv source content checksum mismatch')
                    collector.downloads[relative]['upstream_source_content_sha512'] = expected
                else:
                    collector.fetch(url, relative, digest, 'sha512')
    nginx = next(p for p in packages if p['P'] == 'nginx')['V'].split('-r')[0]
    collector.fetch(f'https://nginx.org/download/nginx-{nginx}.tar.gz', f'runtimes/nginx-{nginx}.tar.gz')
    njs = next(p for p in packages if p['P'] == 'nginx-module-njs')['V'].removeprefix(nginx + '.').split('-r')[0]
    collector.fetch(f'https://codeload.github.com/nginx/njs/tar.gz/refs/tags/{njs}', f'runtimes/njs-{njs}.tar.gz')
    return packages


def java_sources(collector, image):
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads((root / 'tools/container-java-dependencies.json').read_bytes())
    if hashlib.sha256((root / 'business-backend/pom.xml').read_bytes().replace(b'\r\n', b'\n')).hexdigest() != inventory['pom_sha256_lf']:
        raise RuntimeError('Java dependency inventory requires fresh matching CI')
    coordinates_by_filename = {row['filename']: row for row in inventory['components']}
    release = docker(image, 'cat', '/opt/java/openjdk/release').decode()
    runtime = inventory['runtime_source']
    implementor = re.search(r'^IMPLEMENTOR_VERSION="([^"]+)', release, re.M).group(1)
    source_commit = re.search(r'^SOURCE="\.:git:([^"]+)', release, re.M).group(1)
    if implementor != runtime['implementor_version'] or not runtime['source_commit'].startswith(source_commit):
        raise RuntimeError('Java runtime differs from its reviewed source release')
    asset = runtime['archive']
    collector.fetch(asset['browser_download_url'], 'runtimes/' + asset['name'], asset['digest'].removeprefix('sha256:'))
    for field, repository in (('BUILD_SOURCE', 'temurin-build'),):
        commit = re.search(r'^' + field + r'="(?:\.:)?git:([^" ]+)', release, re.M).group(1)
        identity = collector.json(f'https://api.github.com/repos/adoptium/{repository}/commits/{commit}', f'indexes/{repository}-{commit}.json')
        if not identity['sha'].startswith(commit):
            raise RuntimeError('Java source revision prefix mismatch')
        collector.fetch(f'https://codeload.github.com/adoptium/{repository}/tar.gz/{commit}', f'runtimes/{repository}-{commit}.tar.gz', immutable=True)
    container = run('docker', 'create', image).decode().strip()
    jar_path = collector.root / 'application.jar'
    try:
        subprocess.run(['docker', 'cp', container + ':/app/app.jar', str(jar_path)], check=True, stdout=subprocess.DEVNULL)
    finally:
        subprocess.run(['docker', 'rm', container], check=True, stdout=subprocess.DEVNULL)
    packages = []
    observed = set()
    with zipfile.ZipFile(jar_path) as application:
        for entry in application.namelist():
            if not entry.startswith('BOOT-INF/lib/') or not entry.endswith('.jar'):
                continue
            filename = Path(entry).name
            declared = coordinates_by_filename[filename]
            raw = application.read(entry)
            if hashlib.sha256(raw).hexdigest() != declared['sha256']:
                raise RuntimeError('Java dependency bytes differ from the reviewed inventory: ' + filename)
            observed.add(filename)
            with zipfile.ZipFile(io.BytesIO(raw)) as jar:
                props = next((n for n in jar.namelist() if n.startswith('META-INF/maven/') and n.endswith('/pom.properties')), None)
                if props:
                    coordinates = dict(re.findall(r'^(groupId|artifactId|version)=(.+)$', jar.read(props).decode(), re.M))
                    if coordinates != {k: declared[k] for k in ('groupId', 'artifactId', 'version')}:
                        raise RuntimeError('Embedded Maven identity differs from the reviewed inventory')
                else:
                    coordinates = {k: declared[k] for k in ('groupId', 'artifactId', 'version')}
                name = coordinates['artifactId'] + '-' + coordinates['version']
                destination = collector.root / 'licenses/java' / name
                for item in jar.namelist():
                    if item.upper().startswith('META-INF/') and re.search(r'(LICENSE|NOTICE|COPYING)', item.upper()) and not item.endswith('/'):
                        target = destination / item
                        if not target.resolve().is_relative_to(collector.root.resolve()):
                            raise ValueError('Java notice escapes the bundle')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(jar.read(item))
                packages.append(coordinates)
    if observed != set(coordinates_by_filename):
        raise RuntimeError('Java classpath differs from the reviewed inventory')
    for row in packages:
        base = f'https://repo.maven.apache.org/maven2/{row["groupId"].replace(".", "/")}/{row["artifactId"]}/{row["version"]}/'
        name = row['artifactId'] + '-' + row['version']
        for suffix in ('.pom', '-sources.jar'):
            collector.fetch(base + name + suffix, f'java/{row["groupId"]}/{name}{suffix}')
    return {'release': release, 'packages': packages}


def python_sources(collector, image):
    command = """
import importlib.metadata as m
import json
packages = []
for distribution in m.distributions():
    notices = {}
    for item in distribution.files or []:
        path = distribution.locate_file(item)
        if any(word in str(item).lower() for word in ('license', 'notice', 'copying')) and path.is_file() and path.stat().st_size < 2000000:
            notices[str(item)] = path.read_text(errors='replace')
    packages.append({'name': distribution.metadata['Name'], 'version': distribution.version,
        'license': distribution.metadata.get('License-Expression') or distribution.metadata.get('License'), 'notices': notices})
print(json.dumps(packages))
"""
    packages = json.loads(docker(image, 'python', '-c', command))
    for row in packages:
        folder = collector.root / 'licenses/python' / (row['name'] + '-' + row['version'])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'metadata.json').write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
        info = collector.json(f'https://pypi.org/pypi/{row["name"]}/{row["version"]}/json', f'indexes/pypi-{row["name"]}-{row["version"]}.json')
        source = next((r for r in info['urls'] if r['packagetype'] == 'sdist'), None)
        if source:
            collector.fetch(source['url'], 'python/' + source['filename'], source['digests']['sha256'])
        else:
            identity = row['name'].lower(), row['version']
            if identity not in PYTHON_SOURCE_RELEASES:
                raise RuntimeError('Missing Python source: ' + row['name'])
            repository, tag, commit = PYTHON_SOURCE_RELEASES[identity]
            row['source_release'] = {'repository': repository, 'tag': tag, 'commit': commit}
            if row['name'].lower() == 'onnxruntime':
                if not any(name.endswith('/ThirdPartyNotices.txt') for name in row['notices']):
                    raise RuntimeError('ONNX Runtime bundled notices unavailable')
                row['source_release']['url'] = f'https://github.com/{repository}/tree/{commit}'
                row['source_release']['distribution'] = 'MIT core uses immutable upstream source access; bundled MPL Eigen source is mirrored separately'
            else:
                collector.fetch(f'https://codeload.github.com/{repository}/tar.gz/{commit}', f'python/{row["name"]}-{row["version"]}-{commit}.tar.gz', immutable=True)
    info = json.loads(run('docker', 'image', 'inspect', image))[0]
    env = dict(e.split('=', 1) for e in info['Config']['Env'])
    version = env['PYTHON_VERSION']
    collector.fetch(f'https://www.python.org/ftp/python/{version}/Python-{version}.tar.xz', f'runtimes/Python-{version}.tar.xz', env['PYTHON_SHA256'])
    native = wheel_library_sources(collector, image, packages)
    return {'packages': [{k: v for k, v in r.items() if k != 'notices'} for r in packages], 'native_libraries': native}


def archive_notices(collector, archive_path, component):
    with tarfile.open(archive_path) as archive:
        for member in archive.getmembers():
            if (not member.isfile() or len(Path(member.name).parts) > 4 or member.size > 2000000
                    or not re.fullmatch(r'(licen[cs]e|notice|copying|copyright)[^/]*', Path(member.name).name, re.I)):
                continue
            target = collector.root / 'licenses/native' / component / member.name
            if not target.resolve().is_relative_to(collector.root.resolve()):
                raise ValueError('Native notice escapes the bundle')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())


def wheel_library_sources(collector, image, packages):
    root = Path(__file__).resolve().parents[1]
    recipe = json.loads((root / 'tools/container-wheel-sources.json').read_bytes())
    ortools = next(p for p in packages if p['name'].lower() == 'ortools')
    if ortools['version'] != recipe['ortools_version'] or ortools['source_release']['commit'] != recipe['upstream_source']:
        raise RuntimeError('OR-Tools wheel source recipe does not match the installed version')
    source = collector.root / f'python/ortools-{ortools["version"]}-{recipe["upstream_source"]}.tar.gz'
    with tarfile.open(source) as archive:
        member, = [m for m in archive.getmembers() if m.name.endswith('/cmake/dependencies/CMakeLists.txt')]
        cmake = archive.extractfile(member).read().decode()
    for row in recipe['github_sources']:
        block, = [b for b in re.findall(r'FetchContent_Declare\((.*?)\n\s*\)', cmake, re.S) if b.split()[0] == row['component']]
        block = '\n'.join(line for line in block.splitlines() if not line.lstrip().startswith('#'))
        repository = re.search(r'GIT_REPOSITORY\s+"?https://github.com/([^"\s]+?)\.git', block).group(1)
        ref = re.search(r'GIT_TAG\s+"?([^"\s]+)', block).group(1)
        if (repository, ref) != (row['repository'], row['ref']) or not re.fullmatch(r'[a-f0-9]{40}', row['commit']):
            raise RuntimeError('Bundled library source differs from the official OR-Tools recipe')
        path = collector.fetch(f'https://codeload.github.com/{repository}/tar.gz/{row["commit"]}', f'wheel-libraries/{row["component"]}-{row["commit"]}.tar.gz', immutable=True)
        archive_notices(collector, path, row['component'])
    eigen = collector.fetch(recipe['eigen']['url'], 'wheel-libraries/eigen-3.4.0.tar.gz')
    archive_notices(collector, eigen, 'eigen')
    program = """
import importlib.metadata as m
import json
from cryptography.hazmat.backends.openssl.backend import backend
from PIL import features
print(json.dumps({'openssl': backend.openssl_version_text(),
    'pillow': {name: features.version(name) for name in features.get_supported()},
    'ortools_libraries': [str(f) for f in m.distribution('ortools').files if '/.libs/' in str(f)]}))
"""
    native = json.loads(docker(image, 'python', '-c', program))
    onnx = next(p for p in packages if p['name'].lower() == 'onnxruntime')
    onnx_recipe = recipe['onnxruntime']
    if (onnx['version'], onnx['source_release']['commit']) != (onnx_recipe['version'], onnx_recipe['commit']):
        raise RuntimeError('ONNX Runtime source recipe differs from installed package')
    dependencies = collector.fetch(onnx_recipe['deps_url'], 'wheel-libraries/onnxruntime-deps.txt', onnx_recipe['deps_sha256']).read_text()
    eigen = next(line for line in dependencies.splitlines() if line.startswith('eigen;')).split(';')
    if eigen[1:] != [onnx_recipe['eigen_url'], onnx_recipe['eigen_sha1']]:
        raise RuntimeError('ONNX Runtime Eigen source differs from its exact recipe')
    collector.fetch(eigen[1], 'wheel-libraries/onnxruntime-eigen.zip', eigen[2], 'sha1')
    native['onnxruntime_source_recipe'] = onnx_recipe
    version = re.fullmatch(r'OpenSSL (\d+\.\d+\.\d+) .+', native['openssl']).group(1)
    base = f'https://github.com/openssl/openssl/releases/download/openssl-{version}/openssl-{version}.tar.gz'
    digest = collector.fetch(base + '.sha256', f'indexes/openssl-{version}.sha256').read_text().split()[0]
    if not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise RuntimeError('OpenSSL release checksum unavailable')
    archive_notices(collector, collector.fetch(base, f'wheel-libraries/openssl-{version}.tar.gz', digest), 'openssl')
    native['ortools_source_recipe'] = recipe
    (collector.root / 'licenses/python-native-components.json').write_text(json.dumps(native, indent=2) + '\n', encoding='utf-8')
    return native


def npm_sources(collector, root):
    import base64
    packages = json.loads((root / 'frontend/package-lock.json').read_bytes())['packages']
    result = []
    for relative, row in packages.items():
        if not relative or row.get('dev'):
            continue
        algorithm, encoded = row['integrity'].split('-', 1)
        name = relative.rsplit('node_modules/', 1)[1]
        path = collector.fetch(row['resolved'], 'npm/' + name.replace('/', '__') + '-' + row['version'] + '.tgz', base64.b64decode(encoded).hex(), algorithm)
        with tarfile.open(path) as archive:
            notices = [m for m in archive.getmembers() if m.isfile() and re.search(r'(^|/)(licen[cs]e|notice|copying|copyright)[^/]*$', m.name, re.I)]
            if not notices:
                package = json.loads(archive.extractfile('package/package.json').read())
                if package.get('license') != 'MIT':
                    raise RuntimeError('Missing npm notices: ' + name)
                destination = collector.root / 'licenses/npm' / name
                destination.mkdir(parents=True, exist_ok=True)
                (destination / 'upstream-package.json').write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding='utf-8')
                terms = (root / 'LICENSE').read_text().split('Permission is hereby granted', 1)[1]
                (destination / 'MIT-DECLARATION.txt').write_text(
                    'The unchanged upstream package manifest declares SPDX MIT but the published archive contains no separate LICENSE.\n'
                    'Original author/publisher metadata is preserved alongside this standard MIT permission and disclaimer.\n'
                    'No upstream copyright holder or year has been invented.\n\nPermission is hereby granted' + terms, encoding='utf-8')
            for member in notices:
                target = collector.root / 'licenses/npm' / name / member.name
                if not target.resolve().is_relative_to(collector.root.resolve()):
                    raise ValueError('npm notice escapes the bundle')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
        result.append({'name': name, 'version': row['version'], 'license': row.get('license')})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for service in ('agent', 'backend', 'frontend'):
        parser.add_argument('--' + service, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', help='Also write container-sources-vX.Y.Z.zip after collection')
    args = parser.parse_args()
    if args.version and not re.fullmatch(r'v\d+\.\d+\.\d+', args.version):
        parser.error('version must be vX.Y.Z')
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    collector = Collector(args.output.resolve())
    images = {k: json.loads(run('docker', 'image', 'inspect', getattr(args, k)))[0] for k in ('agent', 'backend', 'frontend')}
    metadata = {'images': {k: {'id': v['Id'], 'layers': v['RootFS']['Layers'], 'labels': v['Config']['Labels']} for k, v in images.items()}}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {
            pool.submit(apt_sources, collector, args.agent, 'debian', [('deb.debian.org', 'trixie'), ('deb.debian.org', 'trixie-updates'), ('security.debian.org', 'trixie-security')]): 'debian',
            pool.submit(apt_sources, collector, args.backend, 'ubuntu', [('archive.ubuntu.com', 'resolute'), ('archive.ubuntu.com', 'resolute-updates'), ('security.ubuntu.com', 'resolute-security')]): 'ubuntu',
            pool.submit(python_sources, collector, args.agent): 'python',
            pool.submit(alpine_sources, collector, args.frontend): 'alpine',
            pool.submit(java_sources, collector, args.backend): 'java',
            pool.submit(npm_sources, collector, root): 'npm',
        }
        failures = {}
        for job in as_completed(jobs):
            name = jobs[job]
            try:
                metadata[name] = job.result()
                print('Collected component: ' + name, flush=True)
            except Exception as error:
                failures[name] = f'{type(error).__name__}: {error}'
                print('Failed component: ' + name + ': ' + failures[name], flush=True)
        if failures:
            raise RuntimeError('Source collection incomplete: ' + json.dumps(failures))
    for relative in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'docs/licenses', 'backend/security/native-backports',
                     'tools/container_sources.py', 'tools/container_release.py', 'tools/container-wheel-sources.json',
                     'tools/container-java-dependencies.json', 'docs/evidence/release-v0.1.3-20261109/java.cdx.json'):
        source = root / relative
        target = collector.root / 'project' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copyfile(source, target)
    metadata['downloads'] = dict(sorted(collector.downloads.items()))
    (collector.root / 'manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.version:
        files = {collector.root / p for p in collector.downloads}
        files.update(p for folder in ('licenses', 'project') for p in (collector.root / folder).rglob('*') if p.is_file())
        files.add(collector.root / 'manifest.json')
        sums = ''.join(f'{checksum(p)}  {p.relative_to(collector.root).as_posix()}\n' for p in sorted(files))
        (collector.root / 'SHA256SUMS').write_text(sums, encoding='utf-8')
        files.add(collector.root / 'SHA256SUMS')
        bundle = collector.root.parent / f'container-sources-{args.version}.zip'
        with zipfile.ZipFile(bundle, 'x', compression=zipfile.ZIP_STORED) as archive:
            for path in sorted(files):
                archive.write(path, path.relative_to(collector.root).as_posix())
        with zipfile.ZipFile(bundle) as archive:
            if archive.testzip():
                raise RuntimeError('Corrupt source bundle member')
    print(json.dumps({'source_downloads': len(collector.downloads), 'manifest': str(collector.root / 'manifest.json')}))


if __name__ == '__main__':
    main()
