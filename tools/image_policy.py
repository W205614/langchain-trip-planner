"""Exact distribution assessments; never a whole-package ignore.

Inventory is copied from an exact immutable image using a stopped UUID-owned
container. No image command is executed. Local objects are retained by default;
explicit cleanup is available for the CI runner's own ephemeral environment.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import io
import json
import re
import shlex
from pathlib import Path
from pathlib import PurePosixPath
import subprocess
import tarfile
import uuid
try:
    from . import risk_acceptance
except ImportError:
    import risk_acceptance


BOUND_NOT_AFFECTED = ('not_affected_execution_path', 'not_affected_build_configuration')
APK_RECEIVER_CONFIGURATION = {'Entrypoint': ['python', '/app/receiver.py'], 'Cmd': None, 'User': '1000:1000'}
APK_RECEIVER_ARTIFACT_PATHS = {'primary': '/usr/local/bin/python3.13',
                             'supporting': '/usr/local/lib/libpython3.13.so.1.0'}
APK_RECEIVER_SOCKET = '/usr/local/lib/python3.13/lib-dynload/_socket.cpython-313-x86_64-linux-musl.so'
APK_RECEIVER_PATCH_FILES = {APK_RECEIVER_SOCKET, '/usr/local/lib/python3.13/http/client.py',
                            '/usr/local/lib/python3.13/poplib.py'}


def assessment_package_manager(entry):
    keys = [key for key in ('requires_dpkg', 'requires_apk') if key in entry]
    if len(keys) != 1:
        return None
    values = entry[keys[0]]
    if (not isinstance(values, dict) or not values
            or any(not isinstance(name, str) or not name or not isinstance(version, str) or not version
                   for name, version in values.items())):
        return None
    return 'apk' if keys[0] == 'requires_apk' else 'dpkg'


def apk_assessment_error(policy, entry):
    """Only the receiver's reviewed Alpine binary backport, never an APK waiver."""
    if 'requires_apk' not in entry:
        return 'Alpine assessment requires explicit APK requirements' if entry.get('distribution') == 'alpine' else None
    if (assessment_package_manager(entry) != 'apk' or policy.get('schema_version') != 2
            or policy.get('review_policy') != 'source_and_artifact_bound'
            or entry.get('classification') != 'fixed_by_pinned_security_upgrade'
            or entry.get('artifact_type') != 'binary' or entry.get('distribution') != 'alpine'
            or not isinstance(entry.get('release'), str)
            or not re.fullmatch(r'\d+\.\d+\.\d+', entry['release'])
            or entry.get('requires_architecture') != 'amd64'):
        return 'APK assessment requires exact Alpine amd64 source-bound binary security-upgrade evidence'
    if not isinstance(entry.get('review_record'), str) or entry['review_record'] not in policy.get('source_bindings', {}):
        return 'APK review_record must name a frozen source binding'
    versions = entry.get('artifact_versions')
    if (not isinstance(versions, dict) or set(versions) != {'python'}
            or not isinstance(versions['python'], str) or not versions['python']):
        return 'APK receiver backport requires one exact Python binary version'
    architectures = entry.get('requires_apk_architectures')
    if (not isinstance(architectures, dict) or set(architectures) != set(entry['requires_apk'])
            or any(value not in ('x86_64', 'noarch') for value in architectures.values())
            or architectures.get('musl') != 'x86_64'):
        return 'APK review requires exact per-package architecture; musl must be x86_64'
    hashes = entry.get('file_sha256')
    if (not isinstance(hashes, dict) or not hashes
            or any(not isinstance(path, str) or not path.startswith('/') or '..' in PurePosixPath(path).parts
                   or not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
                   for path, value in hashes.items())):
        return 'APK security upgrade requires exact reviewed file hashes'
    if not APK_RECEIVER_PATCH_FILES.issubset(hashes):
        return 'APK receiver backport must bind the actual socket, HTTP and POP3 patch carriers'
    elf = entry.get('requires_elf_paths')
    if (not isinstance(elf, list) or not elf or any(not isinstance(path, str) for path in elf)
            or len(elf) != len(set(elf)) or not set(elf).issubset(hashes)):
        return 'APK security upgrade requires declared ELF paths bound to the reviewed hashes'
    if (entry.get('requires_artifact_paths') != APK_RECEIVER_ARTIFACT_PATHS
            or not {APK_RECEIVER_SOCKET, *APK_RECEIVER_ARTIFACT_PATHS.values()}.issubset(elf)):
        return 'APK backport requires the exact reviewed Python primary/supporting ELF locations'
    if entry.get('requires_image_configuration') != APK_RECEIVER_CONFIGURATION:
        return 'APK security upgrade is limited to the fixed receiver Entrypoint/Cmd/User contract'
    return None


def apk_artifact_paths_match(artifact):
    locations = artifact.get('locations')
    if not isinstance(locations, list) or len(locations) != 2:
        return False
    observed = {}
    for location in locations:
        if not isinstance(location, dict) or not isinstance(location.get('annotations'), dict):
            return False
        role = location['annotations'].get('evidence')
        if role not in APK_RECEIVER_ARTIFACT_PATHS or role in observed:
            return False
        path = APK_RECEIVER_ARTIFACT_PATHS[role]
        if location.get('path') != path or location.get('accessPath') != path:
            return False
        observed[role] = path
    return observed == APK_RECEIVER_ARTIFACT_PATHS


def bound_assessment_error(policy, entry):
    """Validate the evidence contract for the two source-reviewed assessments."""
    classification = entry.get('classification')
    if classification not in BOUND_NOT_AFFECTED:
        return None
    if policy.get('schema_version') != 2 or policy.get('review_policy') != 'source_and_artifact_bound':
        return 'Not-affected review requires source and artifact bindings'
    review = entry.get('review_record')
    if not isinstance(review, str) or review not in policy.get('source_bindings', {}):
        return 'Not-affected review_record must name a frozen source binding'

    def exact_hashes(values):
        return (isinstance(values, dict) and bool(values)
                and all(isinstance(path, str) and path.startswith('/') and '..' not in PurePosixPath(path).parts
                        and isinstance(digest, str) and len(digest) == 64
                        and all(character in '0123456789abcdef' for character in digest)
                        for path, digest in values.items()))

    if classification == 'not_affected_execution_path':
        sources = entry.get('requires_source_lf_sha256')
        if not exact_hashes(sources) or not any(path.startswith('/app/app/') and path.endswith('.py') for path in sources):
            return 'Execution-path review requires exact LF hashes of application Python sources'
    else:
        if not exact_hashes(entry.get('file_sha256')):
            return 'Build-configuration review requires exact reviewed file hashes'
        if not entry.get('requires_architecture') and not entry.get('requires_file_metadata'):
            return 'Build-configuration review requires architecture or file metadata'
    if 'requires_architecture' in entry and (not isinstance(entry['requires_architecture'], str) or not entry['requires_architecture']):
        return 'Review requires an exact image architecture'
    if 'requires_file_metadata' in entry:
        metadata = entry['requires_file_metadata']
        if (not isinstance(metadata, dict) or not metadata
                or any(not isinstance(path, str) or not path.startswith('/') or '..' in PurePosixPath(path).parts
                       or not isinstance(value, dict) or set(value) != {'mode', 'uid', 'gid'}
                       or any(type(value[field]) is not int or value[field] < 0 for field in ('mode', 'uid', 'gid'))
                       or value['mode'] > 0o7777 for path, value in metadata.items())):
            return 'Review requires exact file mode, uid and gid'
        if classification == 'not_affected_build_configuration' and not set(metadata).issubset(entry['file_sha256']):
            return 'Configuration metadata requires the same files to have exact content hashes'
    return None


def load_policy(path, root, today=None):
    policy = json.loads(Path(path).read_text(encoding='utf-8'))
    if policy.get('schema_version') == 2:
        if policy.get('review_policy') != 'source_and_artifact_bound':
            raise RuntimeError('Distribution assessment requires exact source and artifact bindings')
    elif policy.get('schema_version') == 1:
        today = today or datetime.now(timezone.utc).date()
        if date.fromisoformat(policy['expires_on']) < today:
            raise RuntimeError('Distribution assessment expired; retain raw findings and reassess')
    else:
        raise RuntimeError('Unsupported distribution assessment schema')
    if not policy.get('assessments') or not policy.get('source_bindings'):
        raise RuntimeError('Distribution assessment is incomplete')
    for relative, digest in policy['source_bindings'].items():
        source = (root / relative).resolve()
        if not source.is_relative_to(root.resolve()):
            raise RuntimeError('Distribution source binding escapes repository')
        actual = hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
        if actual != digest:
            raise RuntimeError('Distribution source binding changed: ' + relative)
    for entry in policy['assessments']:
        if policy['schema_version'] == 2 and entry.get('classification') == 'still_vulnerable_vendor_minor':
            raise RuntimeError('Source-bound distribution assessments cannot accept an unfixed vulnerability')
        if (entry.get('classification') not in ('fixed_by_distribution_backport', 'fixed_by_pinned_security_upgrade', 'still_vulnerable_vendor_minor', 'not_affected_component_absent', *BOUND_NOT_AFFECTED)
                or not entry.get('ids') or not entry.get('artifact_versions') or not assessment_package_manager(entry)
                or not entry.get('references') or not entry.get('reason')):
            raise RuntimeError('Distribution entry lacks exact versions, evidence or an honest classification')
        if entry['classification'] == 'still_vulnerable_vendor_minor' and entry.get('vendor_status') != 'vulnerable':
            raise RuntimeError('Vendor severity calibration must retain vulnerable status')
        if entry.get('artifact_type') not in ('deb', 'binary'):
            raise RuntimeError('Distribution assessment cannot suppress language dependencies')
        if entry['classification'] == 'still_vulnerable_vendor_minor' and entry.get('artifact_type') != 'deb':
            raise RuntimeError('Vendor severity calibration requires a distribution package')
        if entry['classification'] == 'still_vulnerable_vendor_minor' and entry.get('vendor_severity') != 'minor_no_dsa':
            raise RuntimeError('Vendor severity calibration requires explicit reviewed Minor/no-DSA evidence')
        if entry['classification'] == 'not_affected_component_absent' and not (entry.get('absent_paths') or entry.get('file_sha256')):
            raise RuntimeError('Component-absence assessment requires actual image file evidence')
        if entry['classification'] == 'fixed_by_pinned_security_upgrade' and not entry.get('file_sha256'):
            raise RuntimeError('Security package upgrade assessment requires the actual reviewed ELF hash')
        if error := bound_assessment_error(policy, entry):
            raise RuntimeError(error)
        if error := apk_assessment_error(policy, entry):
            raise RuntimeError(error)
    if len({assessment_package_manager(entry) for entry in policy['assessments']}) != 1:
        raise RuntimeError('Mixed package managers require separate exact-image policies')
    return policy


def dpkg_inventory(text):
    packages = {}
    for paragraph in text.split('\n\n'):
        fields = {}
        for line in paragraph.splitlines():
            if line and not line[0].isspace() and ': ' in line:
                key, value = line.split(': ', 1)
                fields[key] = value
        if fields.get('Status') == 'install ok installed' and fields.get('Package') and fields.get('Version'):
            packages[fields['Package']] = fields['Version']
    return packages


def apk_inventory(text, architecture):
    """Read installed-db P/V/A identities; file-list fields may repeat.

    apk-tools src/package.c maps P=name, V=version, A=architecture, while
    database.c handles repeated installed-file records. Never skip an opaque
    package paragraph or overwrite an ambiguous identity.
    """
    if architecture != 'x86_64' or not isinstance(text, str) or not text.strip():
        raise RuntimeError('APK installed database or supported architecture unavailable')
    packages, package_architectures = {}, {}
    for paragraph in text.replace('\r\n', '\n').split('\n\n'):
        if not paragraph:
            continue
        fields = {}
        for line in paragraph.splitlines():
            if not line:
                continue
            if len(line) < 2 or not line[0].isascii() or not line[0].isalpha() or line[1] != ':' or '\x00' in line:
                raise RuntimeError('Malformed APK installed database line')
            key, value = line[0], line[2:]
            if key.isupper() and key not in 'CPVASTUILDFMRZ':
                raise RuntimeError('Unknown APK installed database field')
            if key in ('P', 'V', 'A'):
                if key in fields or not value or any(character.isspace() for character in value):
                    raise RuntimeError('Missing or duplicate APK package identity field')
                fields[key] = value
        # apk add --virtual uses dot-prefixed names such as .python-rundeps.
        if set(fields) != {'P', 'V', 'A'} or not re.fullmatch(r'(?:[A-Za-z0-9]|\.[A-Za-z0-9])[A-Za-z0-9+_.-]*', fields['P']):
            raise RuntimeError('APK package identity is incomplete')
        if fields['A'] not in (architecture, 'noarch') or fields['P'] in packages:
            raise RuntimeError('Wrong APK package architecture or duplicate package name')
        packages[fields['P']] = fields['V']
        package_architectures[fields['P']] = fields['A']
    if not packages:
        raise RuntimeError('APK installed inventory is empty')
    return packages, package_architectures


def alpine_release_identity(raw, alpine_release):
    fields = {}
    for line in raw.decode('utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or not re.fullmatch(r'[A-Z_][A-Z0-9_]*', key) or key in fields:
            raise RuntimeError('Malformed or duplicate os-release identity field')
        try:
            values = shlex.split(value, posix=True)
        except ValueError as error:
            raise RuntimeError('Malformed quoted os-release value') from error
        if len(values) != 1:
            raise RuntimeError('Ambiguous os-release value')
        fields[key] = values[0]
    version = fields.get('VERSION_ID', '')
    if (fields.get('ID') != 'alpine' or not re.fullmatch(r'\d+\.\d+\.\d+', version)
            or alpine_release.decode('utf-8').strip() != version):
        raise RuntimeError('Exact Alpine release identities are absent or inconsistent')
    return fields


def exported_file_inventory(archive_path):
    """Stream a retained Docker rootfs tar; never extract or execute its contents."""
    native, application, links, seen = {}, {}, {}, set()
    def safe_path(value):
        path = PurePosixPath('/' + value.lstrip('/'))
        if '..' in path.parts:
            raise RuntimeError('Unsafe image export member')
        return str(path)
    with tarfile.open(archive_path, mode='r|') as archive:
        for member in archive:
            path = safe_path(member.name)
            if member.isdir():
                continue
            if path in seen:
                raise RuntimeError('Duplicate image export member')
            seen.add(path)
            if member.issym() or member.islnk():
                target = member.linkname
                if member.issym() and not target.startswith('/'):
                    target = str(PurePosixPath(path).parent / target)
                # Symlink ../ is normal in an image. Normalize without following
                # anything on the host, then resolve only names in this archive.
                parts = []
                for part in PurePosixPath('/' + target.lstrip('/')).parts:
                    if part in ('/', '.'):
                        continue
                    if part == '..':
                        if not parts:
                            raise RuntimeError('Export link escapes image root')
                        parts.pop()
                    else:
                        parts.append(part)
                links[path] = '/' + '/'.join(parts)
                continue
            if not member.isfile():
                continue
            source = archive.extractfile(member)
            prefix = source.read(4)
            is_elf = prefix == b'\x7fELF'
            is_application = path.startswith('/app/') and '__pycache__' not in PurePosixPath(path).parts and not path.endswith('.pyc')
            if is_application:
                raw = prefix + source.read()
                application[path] = risk_acceptance.application_digest(path, raw)
                if is_elf:
                    native[path] = hashlib.sha256(raw).hexdigest()
            elif is_elf:
                hasher = hashlib.sha256(prefix)
                while block := source.read(1024 * 1024):
                    hasher.update(block)
                native[path] = hasher.hexdigest()
    def resolve(path):
        visited = set()
        while path in links:
            if path in visited:
                return None
            visited.add(path)
            path = links[path]
        return path
    for path in links:
        target = resolve(path)
        if target in native:
            native[path] = native[target]
        if path.startswith('/app/') and target in application:
            application[path] = application[target]
    if not native or not application:
        raise RuntimeError('Complete native/application inventory unavailable')
    return {'native_inventory_complete': True, 'native_elf_sha256': dict(sorted(native.items())),
            'application_file_sha256': dict(sorted(application.items())),
            'application_hash_algorithm': risk_acceptance.APPLICATION_HASH_ALGORITHM}


def exported_requested_files(archive_path, paths):
    """Resolve image-only links; Docker cp errors do not prove file absence."""
    def normalize(value):
        parts = []
        for part in PurePosixPath('/' + value.lstrip('/')).parts:
            if part in ('/', '.'):
                continue
            if part == '..':
                if not parts:
                    raise RuntimeError('Export link escapes image root')
                parts.pop()
            else:
                parts.append(part)
        return '/' + '/'.join(parts)

    result = {}
    with tarfile.open(archive_path) as archive:
        members = {}
        for member in archive:
            path = normalize(member.name)
            if path in members:
                raise RuntimeError('Duplicate image export member')
            members[path] = member
        for original in paths:
            path = normalize(original)
            visited = set()
            for _ in range(41):
                if path in visited:
                    raise RuntimeError('Cyclic image export link')
                visited.add(path)
                parts = PurePosixPath(path).parts[1:]
                for index in range(1, len(parts) + 1):
                    prefix = '/' + '/'.join(parts[:index])
                    member = members.get(prefix)
                    if member and (member.issym() or member.islnk()):
                        target = member.linkname
                        if member.issym() and not target.startswith('/'):
                            target = str(PurePosixPath(prefix).parent / target)
                        path = normalize(target + '/' + '/'.join(parts[index:]))
                        break
                else:
                    member = members.get(path)
                    if member is None:
                        result[original] = None
                    elif not member.isfile():
                        raise RuntimeError('Inventory target is not a regular image file: ' + original)
                    else:
                        result[original] = (archive.extractfile(member).read(),
                                            {'mode': member.mode, 'uid': member.uid, 'gid': member.gid})
                    break
            else:
                raise RuntimeError('Too many image export link traversals')
    return result


def inventory(image, policy, destination, cleanup_owned_resources=False, include_native_elf=False, retained_container=None):
    apk = any('requires_apk' in entry for entry in policy['assessments'])
    if apk and any(assessment_package_manager(entry) != 'apk' for entry in policy['assessments']):
        raise RuntimeError('Mixed or missing APK package manager requirements')
    reused = retained_container is not None
    if reused:
        inspected = subprocess.run(['docker', 'inspect', retained_container], capture_output=True, text=True, timeout=30, check=True)
        observed = json.loads(inspected.stdout)[0]
        name = observed['Name'].lstrip('/')
        if (not name.startswith('trip-security-inventory-') or observed.get('Image') != image
                or observed.get('State', {}).get('Running') or observed.get('State', {}).get('StartedAt') != '0001-01-01T00:00:00Z'
                or observed.get('Config', {}).get('Entrypoint') != ['/bin/false']):
            raise RuntimeError('Reuse requires an exact-image UUID-owned inventory container that has never started')
        container_id = observed['Id']
    else:
        name = 'trip-security-inventory-' + uuid.uuid4().hex[:12]
        result = subprocess.run(['docker', 'create', '--name', name, '--entrypoint', '/bin/false', image],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Cannot create stopped inventory object')
        container_id = result.stdout.strip()
    resource = {'name': name, 'container_id': container_id, 'image_id': image,
                'purpose': 'stopped image inventory extraction; no image command executed',
                'business_data': False, 'status': 'retained', 'cleanup_explicit': cleanup_owned_resources, 'reused': reused}
    resource_path = destination / (name + '-resource.json')
    resource_path.write_text(json.dumps(resource, indent=2) + '\n', encoding='utf-8')
    copied = {}
    metadata = {}
    absent = []
    unresolved = []
    # Debian /etc/os-release is a symlink; use its canonical regular file so
    # stopped-container export does not depend on Docker symlink traversal.
    paths = ({'/lib/apk/db/installed', '/etc/os-release', '/etc/alpine-release', '/var/lib/dpkg/status'}
             if apk else {'/var/lib/dpkg/status', '/usr/lib/os-release'})
    for entry in policy['assessments']:
        paths.update(entry.get('absent_paths', []))
        paths.update(entry.get('file_sha256', {}))
        paths.update(entry.get('requires_source_lf_sha256', {}))
        paths.update(entry.get('requires_file_metadata', {}))
    try:
        # Alpine identity/DB links are resolved only inside the stopped image
        # export. A failed Docker cp is never proof of package absence.
        if apk:
            unresolved = sorted(paths)
        for path in ([] if apk else sorted(paths)):
            if not path.startswith('/') or '..' in Path(path).parts:
                raise RuntimeError('Unsafe inventory path')
            output = subprocess.run(['docker', 'cp', '--follow-link', name + ':' + path, '-'], capture_output=True, timeout=30)
            if output.returncode:
                if b'Could not find the file' in output.stderr or b'No such file or directory' in output.stderr:
                    unresolved.append(path)
                    continue
                raise RuntimeError('Image inventory extraction failed')
            with tarfile.open(fileobj=io.BytesIO(output.stdout)) as archive:
                files = [member for member in archive.getmembers() if member.isfile()]
                if len(files) != 1:
                    unresolved.append(path)
                    continue
                copied[path] = archive.extractfile(files[0]).read()
                metadata[path] = {'mode': files[0].mode, 'uid': files[0].uid, 'gid': files[0].gid}
        native_evidence = {}
        if include_native_elf or unresolved:
            export_path = destination / (name + '-rootfs.tar')
            exported = subprocess.run(['docker', 'export', '--output', str(export_path), container_id],
                                      capture_output=True, timeout=900)
            if exported.returncode:
                raise RuntimeError('Complete stopped-image export failed; no partial inventory accepted')
            if include_native_elf:
                native_evidence = exported_file_inventory(export_path)
            if unresolved:
                for path, record in exported_requested_files(export_path, unresolved).items():
                    if record is None:
                        absent.append(path)
                    else:
                        copied[path], metadata[path] = record
            hasher = hashlib.sha256()
            with export_path.open('rb') as stream:
                while block := stream.read(1024 * 1024):
                    hasher.update(block)
            native_evidence['retained_rootfs_archive'] = {'path': str(export_path), 'sha256': hasher.hexdigest(), 'bytes': export_path.stat().st_size}
    finally:
        if cleanup_owned_resources and not reused:
            cleaned = subprocess.run(['docker', 'rm', '--volumes', name], capture_output=True, timeout=30)
            resource['status'] = 'cleaned_explicit_ci' if cleaned.returncode == 0 else 'cleanup_failed'
            resource_path.write_text(json.dumps(resource, indent=2) + '\n', encoding='utf-8')
            if cleaned.returncode:
                raise RuntimeError('Exact stopped inventory object cleanup failed: ' + name)
    if apk:
        if '/var/lib/dpkg/status' in copied:
            raise RuntimeError('Mixed APK/dpkg package manager inventory is unsupported')
        if not {'/lib/apk/db/installed', '/etc/os-release', '/etc/alpine-release'}.issubset(copied):
            raise RuntimeError('Exact Alpine release or APK installed database unavailable')
        release = alpine_release_identity(copied['/etc/os-release'], copied['/etc/alpine-release'])
    else:
        if '/var/lib/dpkg/status' not in copied or '/usr/lib/os-release' not in copied:
            raise RuntimeError('Distribution identity or dpkg inventory unavailable; missing=' + ','.join(absent))
        release = dict(line.split('=', 1) for line in copied['/usr/lib/os-release'].decode().splitlines() if '=' in line)
    environment_result = subprocess.run(['docker', 'image', 'inspect', '--format', '{{json .Config.Env}}', image],
                                        capture_output=True, text=True, timeout=30, check=True)
    environment_names = [item.split('=', 1)[0] for item in json.loads(environment_result.stdout) or []]
    architecture_result = subprocess.run(['docker', 'image', 'inspect', '--format', '{{json .Architecture}}', image],
                                         capture_output=True, text=True, timeout=30, check=True)
    architecture = json.loads(architecture_result.stdout)
    if not isinstance(architecture, str) or not architecture:
        raise RuntimeError('Image architecture unavailable')
    if apk:
        if architecture != 'amd64':
            raise RuntimeError('APK inventory currently supports only the reviewed amd64 target')
        packages, package_architectures = apk_inventory(copied['/lib/apk/db/installed'].decode('utf-8'), 'x86_64')
        profile_result = subprocess.run(['docker', 'image', 'inspect', '--format',
                                         '{"Entrypoint":{{json .Config.Entrypoint}},"Cmd":{{json .Config.Cmd}},"User":{{json .Config.User}}}', image],
                                        capture_output=True, text=True, timeout=30, check=True)
        image_configuration = json.loads(profile_result.stdout)
        if not isinstance(image_configuration, dict) or set(image_configuration) != {'Entrypoint', 'Cmd', 'User'}:
            raise RuntimeError('Exact image Entrypoint/Cmd/User identity unavailable')
    else:
        packages = dpkg_inventory(copied['/var/lib/dpkg/status'].decode())
    evidence = {'image_id': image, 'distribution': release.get('ID', '').strip('"'),
                'release': release.get('VERSION_ID', '').strip('"'),
                'packages': packages, 'package_manager': 'apk' if apk else 'dpkg',
                'environment_names': environment_names,
                'file_sha256': {path: hashlib.sha256(raw).hexdigest() for path, raw in copied.items()},
                'source_lf_sha256': {path: hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest() for path, raw in copied.items()},
                'file_metadata': metadata, 'architecture': architecture,
                'absent_paths': absent, 'resource_manifest': str(resource_path),
                'method': 'docker cp from exact stopped UUID-owned container; unresolved paths verified through image-only rootfs export/link resolution; no image commands executed; retained locally by default'}
    if apk:
        evidence.update(apk_architecture='x86_64', apk_package_architectures=package_architectures,
                        image_configuration=image_configuration,
                        elf_paths=sorted(path for path, raw in copied.items() if raw.startswith(b'\x7fELF')),
                        method='exact stopped image export; APK P/V/A and Alpine os-release resolved with image-only symlink traversal; no image commands executed')
    evidence.update(native_evidence)
    (destination / 'distribution-inventory.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    return evidence


def assess(policy, match, evidence, acceptance=None):
    if not policy or not evidence:
        return None
    artifact, vulnerability = match.get('artifact', {}), match.get('vulnerability', {})
    for entry in policy['assessments']:
        if bound_assessment_error(policy, entry) or apk_assessment_error(policy, entry):
            continue
        if (evidence.get('distribution') != entry['distribution'] or evidence.get('release') != entry['release']
                or artifact.get('type') != entry['artifact_type']
                or entry['artifact_versions'].get(artifact.get('name')) != artifact.get('version')
                or vulnerability.get('id') not in entry['ids']):
            continue
        manager = assessment_package_manager(entry)
        if manager is None or evidence.get('package_manager', 'dpkg') != manager:
            continue
        if manager == 'apk' and (evidence.get('apk_architecture') != 'x86_64'
                or evidence.get('image_configuration') != entry['requires_image_configuration']
                or not apk_artifact_paths_match(artifact)
                or evidence.get('packages') != entry['requires_apk']
                or evidence.get('apk_package_architectures') != entry['requires_apk_architectures']
                or not set(entry['requires_elf_paths']).issubset(evidence.get('elf_paths', []))):
            continue
        requirements = entry['requires_apk'] if manager == 'apk' else entry['requires_dpkg']
        if any(evidence.get('packages', {}).get(name) != version for name, version in requirements.items()):
            continue
        if set(entry.get('requires_packages_absent', [])).intersection(evidence.get('packages', {})):
            continue
        if set(entry.get('requires_environment_absent', [])).intersection(evidence.get('environment_names', [])):
            continue
        if not set(entry.get('absent_paths', [])).issubset(evidence.get('absent_paths', [])):
            continue
        if any(evidence.get('file_sha256', {}).get(path) != digest for path, digest in entry.get('file_sha256', {}).items()):
            continue
        if any(evidence.get('source_lf_sha256', {}).get(path) != digest for path, digest in entry.get('requires_source_lf_sha256', {}).items()):
            continue
        if 'requires_architecture' in entry and evidence.get('architecture') != entry['requires_architecture']:
            continue
        if any(evidence.get('file_metadata', {}).get(path) != metadata for path, metadata in entry.get('requires_file_metadata', {}).items()):
            continue
        if entry['classification'] == 'still_vulnerable_vendor_minor':
            if (not acceptance or acceptance.get('status') != 'effective_temporary_acceptance_still_vulnerable'
                    or vulnerability.get('id') not in acceptance.get('allowed_cve_ids', [])):
                continue
        return entry['classification']
    return None
