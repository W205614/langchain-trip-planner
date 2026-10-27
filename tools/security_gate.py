"""Pinned scanner CLI with explicit non-clean outcomes. Python 3.11+, no app imports.

python tools/security_gate.py dependencies --reports security-reports
python tools/security_gate.py secrets --reports security-reports
python tools/security_gate.py image --image candidate:tag --reports security-reports
python tools/security_gate.py sarif --directory security-reports/codeql
No container is started, stopped, rebuilt or pruned by this script. Explicit
--vendor-policy extracts inventory through a stopped UUID-owned container retained
locally by default; it never executes image commands. Explicit CI cleanup only.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import Request, urlopen
import zipfile
try:
    from . import image_policy, risk_acceptance, sarif_rules, codeql_context
except ImportError:
    try:
        from tools import image_policy, risk_acceptance, sarif_rules, codeql_context
    except ModuleNotFoundError:
        import image_policy
        import risk_acceptance
        import sarif_rules
        import codeql_context

ROOT = Path(__file__).resolve().parents[1]
PINS = json.loads((ROOT / '.github/security-tool-pins.json').read_text(encoding='utf-8'))


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def tool(name: str) -> str:
    system = platform.system().lower()
    if system not in ('windows', 'linux') or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('Pinned scanner assets support Windows/Linux x86_64 only')
    config = PINS[name][system]
    folder = ROOT / '.cache/security-tools' / name / PINS[name]['version']
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / config['url'].rsplit('/', 1)[-1]
    if not archive.exists():
        request = Request(config['url'], headers={'User-Agent': 'tripplanner-security-gate'})
        with urlopen(request, timeout=120) as source, archive.open('wb') as target:
            shutil.copyfileobj(source, target, length=64 * 1024)
    raw = archive.read_bytes()
    if hashlib.sha256(raw).hexdigest() != config['sha256']:
        raise RuntimeError(f'{name}: downloaded/cached asset failed pinned SHA256 verification')
    if config.get('member'):
        member = config['member']
        if archive.suffix == '.zip':
            with zipfile.ZipFile(io.BytesIO(raw)) as package:
                binary = package.read(member)
        else:
            with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as package:
                entry = package.getmember(member)
                if not entry.isfile():
                    raise RuntimeError('Scanner archive member is not a regular file')
                binary = package.extractfile(entry).read()
        executable = folder / member
        if executable.exists() and hashlib.sha256(executable.read_bytes()).digest() != hashlib.sha256(binary).digest():
            raise RuntimeError('Extracted scanner binary differs from pinned archive')
        if not executable.exists():
            executable.write_bytes(binary)
    else:
        executable = archive
    if system == 'linux':
        executable.chmod(0o755)
    return str(executable)


def run(command: list[str], log: Path, cwd=ROOT, timeout=1200, stdout_json: Path | None = None, env=None):
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, env=env)
        if stdout_json:
            stdout_json.write_text(result.stdout, encoding='utf-8')
            log.write_text(result.stderr, encoding='utf-8')
        else:
            log.write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
        return result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.write_text(type(exc).__name__ + ': scanner unavailable or timed out\n', encoding='utf-8')
        return 125


def report(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None


def classify(code: int, parsed, findings: int, skipped=0, valid_codes=(0, 1)):
    if parsed is None or skipped or code not in valid_codes:
        return 'error'
    if findings:
        return 'blocked_findings'
    return 'pass' if code == 0 else 'error'


def image_document(data):
    return (isinstance(data, dict) and isinstance(data.get('matches'), list)
            and isinstance(data.get('source'), dict) and data['source'].get('type') == 'image')


def source_digest(path: Path):
    return hashlib.sha256(path.read_text(encoding='utf-8').replace('\r\n', '\n').encode('utf-8')).hexdigest()


def load_vex(path: Path, today=None, root=ROOT):
    policy = report(path)
    today = today or datetime.now(timezone.utc).date()
    if not isinstance(policy, dict) or not policy.get('assessments') or not policy.get('source_bindings'):
        raise RuntimeError('VEX policy is incomplete')
    if date.fromisoformat(policy['expires_on']) < today:
        raise RuntimeError('VEX assessment expired; reassess the actual execution path')
    for relative, digest in policy['source_bindings'].items():
        source = (root / relative).resolve()
        if not source.is_relative_to(root.resolve()) or source_digest(source) != digest:
            raise RuntimeError('VEX source binding changed: ' + relative)
    for assessment in policy['assessments']:
        if assessment.get('status') != 'not_affected' or not assessment.get('ids') or not assessment.get('justification') or not assessment.get('references'):
            raise RuntimeError('VEX entry lacks an explicit reviewed not-affected assessment')
    return policy


def contextual_not_affected(policy, name, version, identifiers):
    if not policy:
        return False
    for entry in policy['assessments']:
        if entry.get('ecosystem') == 'PyPI' and entry['package'].lower() == name.lower() and entry['version'] == version and set(entry['ids']).intersection(identifiers):
            return True
    return False


def dependencies(destination: Path, policy=None):
    records = []
    pip_report = destination / 'pip-audit.json'
    code = run([sys.executable, '-m', 'pip_audit', '--no-deps', '--disable-pip', '--strict', '--timeout', '90', '--progress-spinner', 'off',
                '-r', str(ROOT / 'backend/requirements.lock'), '--format', 'json', '--output', str(pip_report)], destination / 'pip-audit.log')
    data = report(pip_report)
    dependencies = data.get('dependencies', []) if isinstance(data, dict) else []
    count = sum(len(item.get('vulns', [])) for item in dependencies)
    contextual = sum(contextual_not_affected(policy, item.get('name', ''), item.get('version', ''), [vulnerability.get('id'), *vulnerability.get('aliases', [])])
                     for item in dependencies for vulnerability in item.get('vulns', []))
    skipped = sum(bool(item.get('skip_reason')) for item in dependencies)
    state = classify(code, data if dependencies else None, count, skipped)
    if state == 'blocked_findings' and contextual == count and policy:
        state = 'pass_contextual'
    records.append({'scanner': 'pip-audit', 'status': state, 'raw_findings': count, 'contextual_not_affected': contextual,
                    'unresolved_findings': count - contextual, 'skipped': skipped, 'exit_code': code,
                    'vex_expires_on': policy.get('expires_on') if policy else None})

    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm') or 'npm'
    npm_report = destination / 'npm-audit.json'
    code = run([npm, 'audit', '--package-lock-only', '--audit-level=high', '--registry=https://registry.npmjs.org', '--json', '--ignore-scripts'],
               destination / 'npm-audit.log', cwd=ROOT / 'frontend', stdout_json=npm_report)
    data = report(npm_report)
    totals = data.get('metadata', {}).get('vulnerabilities', {}) if isinstance(data, dict) else {}
    count = totals.get('high', 0) + totals.get('critical', 0)
    records.append({'scanner': 'npm-audit', 'status': classify(code, data if totals else None, count), 'blocking_findings': count, 'all_severities': totals, 'exit_code': code})

    maven = shutil.which('mvn.cmd' if os.name == 'nt' else 'mvn') or 'mvn'
    code = run([maven, '-B', '-ntp', '-f', str(ROOT / 'business-backend/pom.xml'),
                'org.cyclonedx:cyclonedx-maven-plugin:2.9.3:makeAggregateBom', '-DoutputFormat=json', '-DoutputName=java.cdx',
                '-DoutputDirectory=' + str(destination), '-DincludeTestScope=false', '-Dcyclonedx.skipAttach=true'], destination / 'maven-sbom.log')
    bom = destination / 'java.cdx.json'
    parsed_bom = report(bom)
    if code != 0 or not isinstance(parsed_bom, dict) or not parsed_bom.get('components'):
        records.append({'scanner': 'osv-maven', 'status': 'error', 'reason': 'Resolved nonempty Maven SBOM unavailable', 'exit_code': code})
    else:
        output = destination / 'osv-maven.json'
        code = run([tool('osv-scanner'), 'scan', 'source', '--no-resolve', '--all-packages', '--lockfile=' + str(bom),
                    '--format=json', '--output-file=' + str(output)], destination / 'osv-maven.log')
        data = report(output)
        count = sum(len(package.get('vulnerabilities', [])) for group in (data or {}).get('results', []) for package in group.get('packages', []))
        scanned = sum(len(group.get('packages', [])) for group in (data or {}).get('results', []))
        records.append({'scanner': 'osv-maven', 'status': classify(code, data if scanned else None, count), 'findings': count,
                        'resolved_components': len(parsed_bom['components']), 'scanned_packages': scanned, 'exit_code': code})
    return records


def scan_repository(destination: Path, include_untracked=False):
    executable = tool('gitleaks')
    common = ['--redact=100', '--no-banner', '--log-level=error', '--config=' + str(ROOT / '.gitleaks.toml'), '--report-format=json']
    history = destination / 'gitleaks-history.json'
    code = run([executable, 'git', *common, '--report-path=' + str(history), '--log-opts=--all', str(ROOT)], destination / 'gitleaks-history.log')
    data = report(history)
    records = [{'scanner': 'gitleaks-history', 'status': classify(code, data, len(data or [])), 'findings': len(data or []), 'exit_code': code}]
    # Export only tracked source. Never traverse private runtime files, ignored .env,
    # application uploads, local database volumes or tool caches on a developer host.
    export_root = ROOT / '.cache/security-source-exports'
    export_root.mkdir(parents=True, exist_ok=True)
    export = Path(tempfile.mkdtemp(prefix='tracked-', dir=export_root))
    command = ['git', 'ls-files', '--cached', '-z']
    if include_untracked:
        command.extend(['--others', '--exclude-standard'])
    tracked = subprocess.check_output(command, cwd=ROOT).decode('utf-8').split('\0')
    for relative in filter(None, tracked):
        source = ROOT / relative
        target = export / relative
        if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(ROOT):
            raise RuntimeError('Tracked source unavailable or unsafe: ' + relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    working = destination / 'gitleaks-tracked.json'
    code = run([executable, 'dir', *common, '--report-path=' + str(working), str(export)], destination / 'gitleaks-tracked.log')
    data = report(working)
    records.append({'scanner': 'gitleaks-tracked', 'status': classify(code, data, len(data or [])), 'findings': len(data or []), 'exit_code': code})
    # Retain the exact export for local review; no recursive deletion is performed.
    return records


def image(destination: Path, name: str, policy=None, vendor_policy=None, cleanup_owned_resources=False,
          approval=None, runtime_proof=None, retained_inventory=None, source_mode=None):
    if source_mode is not None:
        try:
            from . import source_equivalence_delta
        except ImportError:
            import source_equivalence_delta
        source_equivalence_delta.validate_release(ROOT, source_mode)
    executable = tool('grype')
    inspected = subprocess.run(['docker', 'image', 'inspect', '--format', '{{json .Id}}', name], capture_output=True, text=True, timeout=30)
    if inspected.returncode:
        raise RuntimeError('Image is not present locally; build/pull the intended revision explicitly before scanning')
    identity = json.loads(inspected.stdout)
    if not isinstance(identity, str) or not identity.startswith('sha256:'):
        raise RuntimeError('Docker did not return an immutable image identity')
    output = destination / 'image-vulnerabilities.json'
    environment = dict(os.environ)
    environment.setdefault('GRYPE_DB_CACHE_DIR', str(ROOT / '.cache/grype-db'))
    environment.setdefault('GRYPE_CHECK_FOR_APP_UPDATE', 'false')
    scan_started = datetime.now(timezone.utc)
    code = run([executable, 'docker:' + identity, '--fail-on', 'high', '--output', 'json', '--file', str(output)], destination / 'image-scan.log', timeout=1800, env=environment)
    data = report(output)
    matches = data.get('matches', []) if isinstance(data, dict) else []
    count = sum(match.get('vulnerability', {}).get('severity', '').lower() in ('high', 'critical') for match in matches)
    if approval and (code not in (0, 2) or not image_document(data)):
        raise RuntimeError('Failed/missing image scan cannot establish temporary risk acceptance; raw receipt retained')
    contextual = 0
    context_verified = False
    if policy or vendor_policy:
        command_info = subprocess.run(['docker', 'image', 'inspect', '--format', '{{json .Config.Cmd}}', identity], capture_output=True, text=True, timeout=30)
        embedded_entrypoint = command_info.returncode == 0 and json.loads(command_info.stdout) == ['uvicorn', 'app.agent_api.main:app', '--host', '0.0.0.0', '--port', '9000', '--workers', '1']
        revision_info = subprocess.run(['docker', 'image', 'inspect', '--format', '{{index .Config.Labels "org.opencontainers.image.revision"}}', identity], capture_output=True, text=True, timeout=30)
        source_revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        context_verified = embedded_entrypoint and revision_info.returncode == 0 and revision_info.stdout.strip() == source_revision
        if embedded_entrypoint and revision_info.returncode == 0 and approval:
            # Default requires complete tree equality. The explicit delta mode
            # requires its separate pinned activation and real clean HEAD proof.
            risk_acceptance.validate_sources(approval, ROOT, revision_info.stdout.strip(), source_mode=source_mode)
            context_verified = True
        if approval and not context_verified:
            raise RuntimeError('Risk acceptance requires the exact approved embedded Agent command and business source context')
        if context_verified and policy:
            for match in matches:
                vulnerability = match.get('vulnerability', {})
                if vulnerability.get('severity', '').lower() not in ('high', 'critical'):
                    continue
                artifact = match.get('artifact', {})
                ids = [vulnerability.get('id'), *[item.get('id') for item in match.get('relatedVulnerabilities', [])]]
                if artifact.get('type') == 'python' and contextual_not_affected(policy, artifact.get('name', ''), artifact.get('version', ''), ids):
                    contextual += 1
    classifications = {'fixed_by_distribution_backport': 0, 'fixed_by_pinned_security_upgrade': 0,
                       'still_vulnerable_vendor_minor': 0, 'not_affected_component_absent': 0}
    assessed = []
    acceptance_metadata = None
    if vendor_policy and context_verified:
        scan_receipt = risk_acceptance.validate_scan_receipt(data, identity, scan_started) if approval else None
        evidence = image_policy.inventory(identity, vendor_policy, destination, cleanup_owned_resources,
                                          include_native_elf=approval is not None, retained_container=retained_inventory)
        if approval:
            acceptance_metadata = risk_acceptance.verify(approval, evidence, runtime_proof, ROOT, revision_info.stdout.strip(), source_mode=source_mode)
            acceptance_metadata['scan_identity_proof'] = scan_receipt
            write(destination / 'risk-acceptance-verification.json', acceptance_metadata)
        for match in matches:
            vulnerability, artifact = match.get('vulnerability', {}), match.get('artifact', {})
            if vulnerability.get('severity', '').lower() not in ('high', 'critical'):
                continue
            classification = image_policy.assess(vendor_policy, match, evidence, acceptance_metadata)
            if classification:
                classifications[classification] += 1
                assessed.append({'package': artifact.get('name'), 'version': artifact.get('version'),
                                 'id': vulnerability.get('id'), 'raw_severity': vulnerability.get('severity'),
                                 'classification': classification})
        write(destination / 'distribution-assessments.json', {'image_id': identity, 'expires_on': vendor_policy['expires_on'], 'assessments': assessed})
    remaining = count - contextual - sum(classifications.values())
    # Grype uses 2 for the requested vulnerability threshold, and 1 for errors.
    # A partial JSON report on an error must never be rescued by a policy.
    state = classify(code, data if image_document(data) else None, count, valid_codes=(0, 2))
    if state == 'blocked_findings' and not remaining:
        state = ('pass_temporary_risk_acceptance' if classifications['still_vulnerable_vendor_minor']
                 else 'pass_vendor_policy' if sum(classifications.values()) else 'pass_contextual')
    return [{'scanner': 'grype-image', 'target': name, 'image_id': identity, 'status': state, 'blocking_findings': remaining,
             'raw_high_critical_findings': count, 'contextual_not_affected': contextual, 'all_findings': len(matches), 'exit_code': code,
             'vex_expires_on': policy.get('expires_on') if policy else None,
             'distribution_assessments': classifications,
             'still_vulnerable_vendor_minor_ids': sorted({item['id'] for item in assessed if item['classification'] == 'still_vulnerable_vendor_minor'}),
             'vendor_policy_expires_on': vendor_policy.get('expires_on') if vendor_policy else None,
             'unfixed_risk_accepted_findings': classifications['still_vulnerable_vendor_minor'],
             'risk_acceptance': acceptance_metadata,
             'image_revision_context_verified': context_verified}]


def sarif(directory: Path, context=None, source_mode=None, language=None):
    delta_proof = None
    if source_mode is not None:
        try:
            from . import source_equivalence_delta
        except ImportError:
            import source_equivalence_delta
        if not context:
            raise RuntimeError('Explicit delta CodeQL mode requires original exact contextual policy')
        delta_proof = source_equivalence_delta.verify_codeql_job(ROOT, directory, context, source_mode, language)
    files = list(directory.rglob('*.sarif'))
    if not files:
        raise RuntimeError('CodeQL SARIF output missing; analysis is not a passing gate')
    records = []
    used_context = set()
    for path in files:
        data = report(path)
        if not isinstance(data, dict) or not isinstance(data.get('runs'), list) or not data['runs']:
            raise RuntimeError('Invalid CodeQL SARIF')
        count = 0
        contextual = []
        for run_data in data['runs']:
            driver = run_data.get('tool', {}).get('driver', {})
            if driver.get('name') != 'CodeQL':
                raise RuntimeError('SARIF was not produced by CodeQL')
            results = run_data.get('results', [])
            if not isinstance(results, list):
                raise RuntimeError('Invalid CodeQL SARIF results array')
            for result in results:
                rule = sarif_rules.resolve_rule(run_data['tool'], result)
                if sarif_rules.is_blocking(rule, result):
                    count += 1
                    assessed = codeql_context.assess(context, rule, result, run_data)
                    if assessed:
                        identity = codeql_context.canonical({k: assessed[k] for k in ('query', 'path', 'region', 'semantic_flow_sha256')})
                        if identity not in used_context:
                            used_context.add(identity)
                            contextual.append(assessed)
        remaining = count - len(contextual)
        records.append({'scanner': 'codeql-policy', 'file': path.name,
                        'raw_high_critical_findings': count, 'not_affected_context_findings': len(contextual),
                        'context_assessments': contextual,
                        'context_expires_at': context.get('expires_at') if context else None,
                        'blocking_findings': remaining,
                        'status': 'blocked_findings' if remaining else 'pass_codeql_context' if contextual else 'pass'})
        if delta_proof is not None:
            records[-1]['source_equivalence_job_proof'] = delta_proof
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['dependencies', 'secrets', 'image', 'sarif', 'install'])
    parser.add_argument('--reports', type=Path, default=Path('security-reports'))
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--codeql-context', type=Path, help='Explicit exact query/location/source/semantic-flow false-positive review; raw severity/findings are preserved, not vulnerability acceptance')
    parser.add_argument('--source-equivalence', choices=['restore-response-v1'], help='Named exact delta; disabled until separate pinned activation, never a CLI grant')
    parser.add_argument('--codeql-language', choices=['java-kotlin', 'python', 'javascript-typescript', 'actions'])
    parser.add_argument('--image')
    parser.add_argument('--tool', choices=['gitleaks', 'osv-scanner', 'grype'])
    parser.add_argument('--include-untracked', action='store_true', help='Also scan new nonignored local files before commit')
    parser.add_argument('--vex', type=Path, help='Explicit opt-in to an unexpired source-bound contextual assessment; raw findings remain in reports')
    parser.add_argument('--vendor-policy', type=Path, help='Explicit source/revision/dpkg-bound distribution backport or vendor severity assessment; vulnerable entries stay visible')
    parser.add_argument('--risk-acceptance', type=Path, help='Explicit human-approved, expiring local-demo-only acceptance with exact business/native/package/runtime bindings; never a fix')
    parser.add_argument('--runtime-proof', type=Path, help='Fresh actual-container proof from tools/runtime_constraint_probe.py; mandatory for residual risk acceptance')
    parser.add_argument('--inventory-container', help='Reuse exact-image UUID-owned stopped inventory object that has never started; always retained even with CI cleanup flag')
    parser.add_argument('--cleanup-owned-resources', action='store_true', help='CI ephemeral runner only: remove only stopped UUID-owned inventory objects; local default retains them for the authorized cleanup window')
    args = parser.parse_args()
    destination = args.reports.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    try:
        policy = load_vex(args.vex) if args.vex else None
        vendor_policy = image_policy.load_policy(args.vendor_policy, ROOT) if args.vendor_policy else None
        if args.risk_acceptance and (args.mode != 'image' or not vendor_policy or not args.runtime_proof):
            raise RuntimeError('Explicit risk acceptance requires image scan, vendor policy and actual runtime proof')
        if (args.runtime_proof or args.inventory_container) and not args.risk_acceptance:
            raise RuntimeError('Runtime/inventory acceptance options require --risk-acceptance')
        approval = risk_acceptance.load(args.risk_acceptance, ROOT) if args.risk_acceptance else None
        if args.source_equivalence and not ((args.mode == 'image' and approval) or (args.mode == 'sarif' and args.codeql_context and args.codeql_language)):
            raise RuntimeError('Source equivalence applies only to explicit Agent acceptance or current CodeQL language context')
        runtime_proof = report(args.runtime_proof) if args.runtime_proof else None
        if args.runtime_proof and not isinstance(runtime_proof, dict):
            raise RuntimeError('Runtime constraint receipt missing or malformed')
        if args.codeql_context and args.mode != 'sarif':
            raise RuntimeError('--codeql-context applies only to SARIF source-context review')
        context = codeql_context.load(args.codeql_context, ROOT, source_mode=args.source_equivalence) if args.codeql_context else None
        if args.mode == 'install':
            print(tool(args.tool or 'osv-scanner'))
            return 0
        if args.mode == 'sarif':
            records = sarif(args.directory or destination, context, args.source_equivalence, args.codeql_language)
        elif args.mode == 'image':
            if not args.image:
                raise RuntimeError('--image is required')
            records = image(destination, args.image, policy, vendor_policy, args.cleanup_owned_resources,
                            approval, runtime_proof, args.inventory_container, args.source_equivalence)
        elif args.mode == 'secrets':
            records = scan_repository(destination, args.include_untracked)
        else:
            records = dependencies(destination, policy)
        write(destination / (args.mode + '-summary.json'), {'tool_pins_verified_date': PINS['verified_date'], 'results': records})
        print(json.dumps(records, ensure_ascii=False))
        return 0 if all(record['status'] in ('pass', 'pass_contextual', 'pass_vendor_policy', 'pass_temporary_risk_acceptance', 'pass_codeql_context') for record in records) else 1
    except Exception as exc:
        write(destination / (args.mode + '-summary.json'), {'status': 'error', 'error_type': type(exc).__name__, 'message': str(exc)})
        print(f'Security gate unavailable: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
