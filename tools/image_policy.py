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
                or not entry.get('ids') or not entry.get('artifact_versions') or not entry.get('requires_dpkg')
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
    paths = {'/var/lib/dpkg/status', '/usr/lib/os-release'}
    for entry in policy['assessments']:
        paths.update(entry.get('absent_paths', []))
        paths.update(entry.get('file_sha256', {}))
        paths.update(entry.get('requires_source_lf_sha256', {}))
        paths.update(entry.get('requires_file_metadata', {}))
    try:
        for path in sorted(paths):
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
    evidence = {'image_id': image, 'distribution': release.get('ID', '').strip('"'),
                'release': release.get('VERSION_ID', '').strip('"'),
                'packages': dpkg_inventory(copied['/var/lib/dpkg/status'].decode()),
                'environment_names': environment_names,
                'file_sha256': {path: hashlib.sha256(raw).hexdigest() for path, raw in copied.items()},
                'source_lf_sha256': {path: hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest() for path, raw in copied.items()},
                'file_metadata': metadata, 'architecture': architecture,
                'absent_paths': absent, 'resource_manifest': str(resource_path),
                'method': 'docker cp from exact stopped UUID-owned container; unresolved paths verified through image-only rootfs export/link resolution; no image commands executed; retained locally by default'}
    evidence.update(native_evidence)
    (destination / 'distribution-inventory.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    return evidence


def assess(policy, match, evidence, acceptance=None):
    if not policy or not evidence:
        return None
    artifact, vulnerability = match.get('artifact', {}), match.get('vulnerability', {})
    for entry in policy['assessments']:
        if bound_assessment_error(policy, entry):
            continue
        if (evidence.get('distribution') != entry['distribution'] or evidence.get('release') != entry['release']
                or artifact.get('type') != entry['artifact_type']
                or entry['artifact_versions'].get(artifact.get('name')) != artifact.get('version')
                or vulnerability.get('id') not in entry['ids']):
            continue
        if any(evidence.get('packages', {}).get(name) != version for name, version in entry['requires_dpkg'].items()):
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
