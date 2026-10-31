"""Read-only host capacity and project container inventory; never starts services."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess


def inspect_paths(paths, min_free_ratio=0.1):
    if not 0 < min_free_ratio < 1:
        raise ValueError('min-free-ratio must be between 0 and 1')
    result = []
    for value in paths:
        path = Path(value).resolve(strict=True)
        usage = shutil.disk_usage(path if path.is_dir() else path.parent)
        ratio = usage.free / usage.total
        result.append({'path': str(path), 'total_bytes': usage.total, 'free_bytes': usage.free,
                       'free_ratio': round(ratio, 6), 'below_warning': ratio < min_free_ratio,
                       'file_bytes': path.stat().st_size if path.is_file() else None})
    return result


def project_containers(project):
    result = subprocess.run(['docker', 'ps', '-a', '--filter', f'label=com.docker.compose.project={project}',
                             '--format', '{{json .}}'], capture_output=True, text=True, encoding='utf-8', check=True, timeout=15)
    return [{'name': row['Names'], 'state': row['State'], 'status': row['Status']}
            for line in result.stdout.splitlines() if line for row in [json.loads(line)]]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', action='append', type=Path,
                        help='Existing project data directory, Docker storage directory or VHDX; repeat for separate disks')
    parser.add_argument('--min-free-ratio', type=float, default=0.1)
    parser.add_argument('--project', help='Optional exact Docker Compose project for read-only inventory')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    capacities = inspect_paths(args.path or [Path(__file__).resolve().parents[1]], args.min_free_ratio)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'mode': 'read_only_single_host',
              'capacity_within_warning': all(not item['below_warning'] for item in capacities),
              'warning_free_ratio': args.min_free_ratio, 'host_filesystems': capacities,
              'containers': project_containers(args.project) if args.project else None,
              'boundary': 'Host filesystem capacity only. VHDX file bytes do not measure Linux free space, PostgreSQL WAL, backup validity, application health or provider availability. No services or backup schedules are changed.'}
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    print(rendered)
    return 0 if report['capacity_within_warning'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
