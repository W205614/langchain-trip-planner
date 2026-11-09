"""Collect sources and notices for the exact Linux/amd64 application images.

Uses distribution source indexes, installed package metadata and locked npm
archives. Missing identities or checksums stop collection before publication.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.client import IncompleteRead
import io
import json
import lzma
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile


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

    def fetch(self, url, relative, expected=None, algorithm='sha256'):
        relative = relative.replace(':', '%3A')
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('Source output path escapes the bundle')
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            print('Downloading ' + relative, flush=True)
            temporary = path.with_name(path.name + '.part')
            # Observed truncated HTTPS bodies must not become cached sources.
            # Retry transport failures only; HTTP errors and bad hashes fail.
            for attempt in range(3):
                try:
                    with urlopen(Request(url, headers={'User-Agent': 'trip-container-sources'}), timeout=120) as response:
                        length = response.headers.get('Content-Length')
                        with temporary.open('wb') as output:
                            shutil.copyfileobj(response, output)
                        if length and temporary.stat().st_size != int(length):
                            raise IncompleteRead(b'', int(length) - temporary.stat().st_size)
                    break
                except HTTPError as error:
                    raise RuntimeError(f'Source HTTP {error.code}: {url}') from error
                except (IncompleteRead, URLError, TimeoutError, ConnectionError):
                    if attempt == 2:
                        raise
                    print('Retrying truncated/failed transport: ' + relative, flush=True)
            if expected and checksum(temporary, algorithm) != expected:
                raise RuntimeError('Source checksum mismatch: ' + relative)
            temporary.replace(path)
        if expected and checksum(path, algorithm) != expected:
            raise RuntimeError('Source checksum mismatch: ' + relative)
        self.downloads[relative] = {'url': url, 'sha256': checksum(path), 'bytes': path.stat().st_size,
                                    'upstream_checksum': expected, 'algorithm': algorithm if expected else None}
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
        raw = collector.fetch(f'https://gitlab.alpinelinux.org/alpine/aports/-/archive/{commit}/aports-{commit}.tar.gz?path=main/{origin}', f'alpine/recipes/{origin}-{commit}.tar.gz')
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
                collector.fetch('https://distfiles.alpinelinux.org/distfiles/v3.24/' + filename, 'alpine/distfiles/' + filename, digest, 'sha512')
    nginx = next(p for p in packages if p['P'] == 'nginx')['V'].split('-r')[0]
    collector.fetch(f'https://nginx.org/download/nginx-{nginx}.tar.gz', f'runtimes/nginx-{nginx}.tar.gz')
    njs = next(p for p in packages if p['P'] == 'nginx-module-njs')['V'].removeprefix(nginx + '.').split('-r')[0]
    collector.fetch(f'https://codeload.github.com/nginx/njs/tar.gz/refs/tags/{njs}', f'runtimes/njs-{njs}.tar.gz')
    return packages


def java_sources(collector, image):
    release = docker(image, 'cat', '/opt/java/openjdk/release').decode()
    for field, repository in (('SOURCE', 'jdk21u'), ('BUILD_SOURCE', 'temurin-build')):
        commit = re.search(r'^' + field + r'="(?:\.:)?git:([^" ]+)', release, re.M).group(1)
        collector.fetch(f'https://codeload.github.com/adoptium/{repository}/tar.gz/{commit}', f'runtimes/{repository}-{commit}.tar.gz')
    container = run('docker', 'create', image).decode().strip()
    jar_path = collector.root / 'application.jar'
    try:
        subprocess.run(['docker', 'cp', container + ':/app/app.jar', str(jar_path)], check=True, stdout=subprocess.DEVNULL)
    finally:
        subprocess.run(['docker', 'rm', container], check=True, stdout=subprocess.DEVNULL)
    packages = []
    with zipfile.ZipFile(jar_path) as application:
        for entry in application.namelist():
            if not entry.startswith('BOOT-INF/lib/') or not entry.endswith('.jar'):
                continue
            with zipfile.ZipFile(io.BytesIO(application.read(entry))) as jar:
                props = next((n for n in jar.namelist() if n.startswith('META-INF/maven/') and n.endswith('/pom.properties')), None)
                if props:
                    coordinates = dict(re.findall(r'^(groupId|artifactId|version)=(.+)$', jar.read(props).decode(), re.M))
                else:
                    match = re.fullmatch(r'(spring-[a-z-]+)-(\d[^/]+)\.jar', Path(entry).name)
                    if not match:
                        raise RuntimeError('Missing Maven identity: ' + entry)
                    artifact, version = match.groups()
                    coordinates = {'groupId': 'org.springframework.boot' if artifact.startswith('spring-boot') else 'org.springframework', 'artifactId': artifact, 'version': version}
                name = coordinates['artifactId'] + '-' + coordinates['version']
                destination = collector.root / 'licenses/java' / name
                for item in jar.namelist():
                    if item.upper().startswith('META-INF/') and re.search(r'(LICENSE|NOTICE|COPYING)', item.upper()) and not item.endswith('/'):
                        target = destination / Path(item).name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(jar.read(item))
                packages.append(coordinates)
    for row in packages:
        base = f'https://repo.maven.apache.org/maven2/{row["groupId"].replace(".", "/")}/{row["artifactId"]}/{row["version"]}/'
        name = row['artifactId'] + '-' + row['version']
        for suffix in ('.pom', '-sources.jar'):
            collector.fetch(base + name + suffix, f'java/{row["groupId"]}/{name}{suffix}')
    return {'release': release, 'packages': packages}


def python_sources(collector, image):
    command = "import importlib.metadata as m,json; print(json.dumps([{'name':d.metadata['Name'],'version':d.version,'license':d.metadata.get('License-Expression') or d.metadata.get('License'), 'notices':{str(f):d.locate_file(f).read_text(errors='replace') for f in d.files or [] if any(s in str(f).lower() for s in ('license','notice','copying')) and d.locate_file(f).is_file() and d.locate_file(f).stat().st_size<2000000} for d in m.distributions()]))"
    packages = json.loads(docker(image, 'python', '-c', command))
    for row in packages:
        folder = collector.root / 'licenses/python' / (row['name'] + '-' + row['version'])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'metadata.json').write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
        info = collector.json(f'https://pypi.org/pypi/{row["name"]}/{row["version"]}/json', f'indexes/pypi-{row["name"]}-{row["version"]}.json')
        source = next((r for r in info['urls'] if r['packagetype'] == 'sdist'), None)
        if source:
            collector.fetch(source['url'], 'python/' + source['filename'], source['digests']['sha256'])
        elif row['name'].lower() != 'onnxruntime':
            raise RuntimeError('Missing Python source: ' + row['name'])
        else:
            collector.fetch(f'https://codeload.github.com/microsoft/onnxruntime/tar.gz/refs/tags/v{row["version"]}', f'python/onnxruntime-{row["version"]}.tar.gz')
    info = json.loads(run('docker', 'image', 'inspect', image))[0]
    env = dict(e.split('=', 1) for e in info['Config']['Env'])
    version = env['PYTHON_VERSION']
    collector.fetch(f'https://www.python.org/ftp/python/{version}/Python-{version}.tar.xz', f'runtimes/Python-{version}.tar.xz', env['PYTHON_SHA256'])
    return [{k: r[k] for k in ('name', 'version', 'license')} for r in packages]


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
                raise RuntimeError('Missing npm notices: ' + name)
            for member in notices:
                target = collector.root / 'licenses/npm' / name / member.name
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
    metadata['debian'] = apt_sources(collector, args.agent, 'debian', [('deb.debian.org', 'trixie'), ('deb.debian.org', 'trixie-updates'), ('security.debian.org', 'trixie-security')])
    metadata['ubuntu'] = apt_sources(collector, args.backend, 'ubuntu', [('archive.ubuntu.com', 'resolute'), ('archive.ubuntu.com', 'resolute-updates'), ('security.ubuntu.com', 'resolute-security')])
    metadata['alpine'] = alpine_sources(collector, args.frontend)
    metadata['java'] = java_sources(collector, args.backend)
    metadata['python'] = python_sources(collector, args.agent)
    metadata['npm'] = npm_sources(collector, root)
    for relative in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'docs/licenses', 'backend/security/native-backports'):
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
