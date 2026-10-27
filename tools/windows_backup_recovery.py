"""Host-only compensation under the existing per-project maintenance lock.

An interrupted backup remains failed even if the original running services can
be restored. Never recreate containers or start an originally stopped service.
"""
from __future__ import annotations

import json
import time

ROLES = frozenset({'backend', 'agent', 'postgres', 'frontend'})


def _identity(before, current, project, role):
    labels = current.get('Config', {}).get('Labels') or {}
    original_labels = before.get('Config', {}).get('Labels') or {}
    if (before.get('Id') != current.get('Id') or before.get('Image') != current.get('Image')
            or before.get('Mounts') != current.get('Mounts') or labels != original_labels
            or labels.get('com.docker.compose.project') != project
            or labels.get('com.docker.compose.service') != role
            or labels.get('com.docker.compose.oneoff', '').lower() != 'false'):
        raise RuntimeError('Original backup container identity changed; recovery refused')


def restore_original(maintenance, original, project, *, inspect=None, wait_ready=None):
    """Only exact original IDs, all metadata checked before the first start.

    Injectable read/probe functions support failure tests without Docker. The
    normal path uses the same bounded container probes as the backup module.
    """
    result = {'attempted': True, 'passed': False, 'actions': [], 'errors': [],
              'scope': 'Only originally running four exact project IDs; no recreate, stop, delete or other-project operation.'}
    if set(original) != ROLES:
        result['errors'].append({'step': 'identity_preflight', 'error_type': 'IncompleteOriginalServices'})
        return result
    inspect = inspect or (lambda identity: json.loads(maintenance.docker('inspect', identity))[0])

    def ready(role, item):
        if wait_ready:
            wait_ready(role, item)
            return
        if role == 'postgres':
            values = dict(value.split('=', 1) for value in item['Config']['Env'] if '=' in value)
            for attempt in range(10):
                try:
                    maintenance.docker('exec', item['Id'], 'pg_isready', '-t', '5', '-U', values.get('POSTGRES_USER', 'trip'))
                    return
                except Exception:
                    if attempt == 9:
                        raise
                    time.sleep(1)
        elif role in {'backend', 'agent'}:
            # Bounded retries; one HTTP probe already has a five-second timeout.
            for attempt in range(10):
                try:
                    if maintenance._container_probe(item['Id'], role)[0] == 200:
                        return
                except Exception:
                    pass
                if attempt < 9:
                    time.sleep(1)
            raise RuntimeError('Original service is not ready after compensation')

    try:
        for role, item in original.items():
            _identity(item, inspect(item['Id']), project, role)
    except Exception as error:
        result['errors'].append({'step': 'identity_preflight', 'error_type': type(error).__name__})
        return result
    for role in ('postgres', 'backend', 'agent', 'frontend'):
        item = original[role]
        if not item['State']['Running']:
            continue
        try:
            current = inspect(item['Id'])
            _identity(item, current, project, role)
            if not current['State']['Running']:
                maintenance.docker('start', item['Id'])
                result['actions'].append({'service': role, 'id': item['Id'], 'operation': 'start'})
            ready(role, item)
        except Exception as error:
            result['errors'].append({'step': 'dependency_restore', 'service': role, 'error_type': type(error).__name__})
            # Do not start dependents behind an unavailable PostgreSQL/backend.
            if role in {'postgres', 'backend'}:
                return result
    try:
        result['original_running_states_restored'] = True
        for role, item in original.items():
            current = inspect(item['Id'])
            _identity(item, current, project, role)
            if current['State']['Running'] != item['State']['Running']:
                result['original_running_states_restored'] = False
        result['passed'] = not result['errors'] and result['original_running_states_restored']
    except Exception as error:
        result['errors'].append({'step': 'final_snapshot', 'error_type': type(error).__name__})
    return result


def execute_backup(maintenance, project, backup_root, replica_root=None, execute=False):
    """Keep compensation inside the existing lock; never turn failure green."""
    with maintenance.maintenance_lock(project):
        original = maintenance.services(project) if execute else None
        try:
            return maintenance._run_backup_locked(project, backup_root, replica_root, execute)
        except maintenance.MaintenanceFailure as error:
            if execute and error.report.get('execution_started'):
                error.report['failure_recovery'] = restore_original(maintenance, original, project)
                # Preserve the failed backup/finally evidence and old success marker.
                error.report['passed'] = False
                error.report['backup_failure_preserved_after_recovery'] = True
            raise
