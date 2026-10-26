"""Build-only removal and read-only verification of four unused runtime CLIs.

Does not remove packages, libraries or their vulnerability records. The manifest
binds the original binaries so a renamed hardlink or byte-identical copy in the
bounded executable search directories is also rejected by runtime verification.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat

TOOLS = frozenset({'mount', 'umount', 'nsenter', 'unshare'})
BIN_DIRS = ('usr/bin', 'bin', 'usr/sbin', 'sbin', 'usr/local/bin', 'usr/local/sbin')
LIBRARIES = ('usr/lib/x86_64-linux-gnu/libc.so.6', 'usr/lib/x86_64-linux-gnu/libstdc++.so.6',
             'usr/lib/x86_64-linux-gnu/libmount.so.1', 'usr/lib/x86_64-linux-gnu/libblkid.so.1',
             'usr/lib/x86_64-linux-gnu/libgcc_s.so.1')
DEFAULT_MANIFEST = '/usr/local/share/tripplanner/restricted-runtime-tools.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe(root, relative):
    parts = PurePosixPath(relative.lstrip('/')).parts
    if '..' in parts:
        raise RuntimeError('Refusing a parent-traversal inventory path')
    path = root.joinpath(*parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError('Inventory path resolves outside the selected image root')
    return path


def capabilities(path):
    try:
        return os.getxattr(path, 'security.capability', follow_symlinks=False).hex()
    except OSError as exc:
        if exc.errno in {errno.ENODATA, getattr(errno, 'ENOATTR', errno.ENODATA)}:
            return None
        raise RuntimeError('Cannot verify file capabilities: ' + str(path)) from exc


def package_paths(root):
    result = set()
    directory = root / 'var/lib/dpkg/info'
    for pattern in ('mount*.list', 'util-linux*.list'):
        for listing in directory.glob(pattern):
            for line in listing.read_text(encoding='utf-8').splitlines():
                if PurePosixPath(line).name in TOOLS:
                    path = safe(root, line)
                    executable_directory = path.parent in {safe(root, value) for value in BIN_DIRS}
                    if path.is_dir():
                        # dpkg also records /usr/share/doc/mount. That is not a
                        # CLI. A named directory inside a bin directory remains
                        # an abnormal target and must reach the refusal below.
                        if executable_directory:
                            result.add(path)
                    elif path.is_symlink() or (path.is_file() and
                            (executable_directory or path.stat().st_mode & 0o111)):
                        result.add(path)
    return result


def candidates(root):
    result = package_paths(root)
    for relative in BIN_DIRS:
        directory = safe(root, relative)
        if directory.is_dir():
            result.update(directory.iterdir())
    if len(result) > 20000:
        raise RuntimeError('Executable inventory exceeded the bounded review scope')
    return result


def collect_fingerprints(root):
    found = {}
    for relative in BIN_DIRS:
        for tool in TOOLS:
            path = safe(root, relative + '/' + tool)
            if path.is_file():
                info = path.stat()
                found[digest(path)] = {'sha256': digest(path), 'size': info.st_size}
    for path in package_paths(root):
        if path.is_file():
            info = path.stat()
            found[digest(path)] = {'sha256': digest(path), 'size': info.st_size}
    return list(found.values())


def scan(root, fingerprints, capability_reader=capabilities):
    by_size = {}
    for entry in fingerprints:
        by_size.setdefault(entry['size'], set()).add(entry['sha256'])
    rows = []
    for path in sorted(candidates(root)):
        if not path.exists() and not path.is_symlink():
            continue
        # Name, then content identity covers usrmerge aliases, hardlinks and
        # exact copied binaries without deleting unrelated executable files.
        named = path.name in TOOLS
        matched = False
        if path.is_file():
            info = path.stat()
            matched = info.st_size in by_size and digest(path) in by_size[info.st_size]
        if not named and not matched:
            continue
        info = path.lstat()
        rows.append({'path': '/' + str(path.relative_to(root)).replace(os.sep, '/'),
                     'symlink': path.is_symlink(), 'mode': oct(stat.S_IMODE(info.st_mode)),
                     'setuid': bool(info.st_mode & stat.S_ISUID),
                     'file_capability': capability_reader(path),
                     'reason': 'restricted_name' if named else 'restricted_binary_identity'})
    return rows


def restrict(root, capability_reader=capabilities):
    fingerprints = collect_fingerprints(root)
    if not fingerprints:
        raise RuntimeError('No original restricted tool fingerprints; do not fabricate removal evidence')
    libraries = {name: digest(safe(root, name)) for name in LIBRARIES if safe(root, name).is_file()}
    database = root / 'var/lib/dpkg/status'
    before_dpkg = digest(database) if database.is_file() else None
    before = scan(root, fingerprints, capability_reader)
    for row in before:
        path = safe(root, row['path'])
        if path.is_dir():
            raise RuntimeError('Refusing to remove a directory from CLI inventory')
        path.unlink(missing_ok=True)
    remaining = scan(root, fingerprints, capability_reader)
    if remaining:
        raise RuntimeError('Restricted CLI alias/copy still present after image cleanup')
    if any(not safe(root, name).is_file() or digest(safe(root, name)) != value for name, value in libraries.items()):
        raise RuntimeError('Core library changed during CLI-only cleanup')
    if before_dpkg is not None and digest(database) != before_dpkg:
        raise RuntimeError('Package database changed during CLI-only cleanup')
    return {'schema_version': 1, 'status': 'verified_absent', 'tools': sorted(TOOLS),
            'search_directories': list(BIN_DIRS), 'fingerprints': fingerprints,
            'removed_entries': before, 'remaining_entries': remaining,
            'unchanged_library_sha256': libraries, 'package_database_sha256': before_dpkg,
            'limits': 'Bounded name/dpkg/exact-binary-identity inventory; does not prove absence of arbitrary recompiled helpers or vulnerability absence in retained libraries/static native consumers.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    parser.add_argument('--manifest', type=Path, default=Path(DEFAULT_MANIFEST))
    args = parser.parse_args()
    root = Path('/')
    if args.apply:
        if os.name != 'posix' or os.geteuid() != 0 or os.environ.get('TRIP_SECURITY_IMAGE_BUILD') != '1':
            raise RuntimeError('Apply is restricted to the explicit root Docker build step')
        manifest = restrict(root)
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        args.manifest.chmod(0o644)
        print(json.dumps(manifest))
        return 0
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    if set(manifest.get('tools', [])) != TOOLS or not manifest.get('fingerprints'):
        raise RuntimeError('Restricted CLI manifest is absent or incomplete')
    remaining = scan(root, manifest['fingerprints'])
    print(json.dumps({'status': 'verified_absent' if not remaining else 'restricted_tools_present',
                      'remaining_entries': remaining, 'manifest': str(args.manifest),
                      'limits': manifest['limits']}))
    return 1 if remaining else 0


if __name__ == '__main__':
    raise SystemExit(main())
