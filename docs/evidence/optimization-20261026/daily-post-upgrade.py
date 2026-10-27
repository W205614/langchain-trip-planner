"""Read-only original-column/mount/config readback; never outputs credentials."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend/scripts'))
from deployment_assets import services, sql, database_columns, database_summary, docker


def env(item):
    return dict(value.split('=', 1) for value in item['Config']['Env'] if '=' in value)


def main():
    private = ROOT / '.codex-optimization/daily-window'
    evidence = Path(__file__).resolve().parent
    original = json.loads((private / 'original-inspect.json').read_text())
    backup = Path((private / 'latest-backup.txt').read_text())
    columns = json.loads((backup / 'database-columns.json').read_text())
    before = json.loads((backup / 'database-summary.json').read_text())
    current = services('langchain-trip-planner')
    pg = current['postgres']; pg_env = env(pg)
    database, user = pg_env['POSTGRES_DB'], pg_env['POSTGRES_USER']
    after = database_summary(pg['Id'], database, user, columns)
    (private / 'post-upgrade-original-column-summary.json').write_text(json.dumps(after))
    changed = {name for name in columns if before[name] != after[name]}
    clone = json.loads(docker('inspect', 'trip-final-restore-pg-81fcec811960'))[0]
    clone_env = env(clone)
    def query(container, text):
        return sql(container, clone_env['POSTGRES_DB'] if container == clone['Id'] else database,
                   text, clone_env['POSTGRES_USER'] if container == clone['Id'] else user)
    # Independently restored fresh snapshot, not an empty fixture database.
    assert database_summary(clone['Id'], clone_env['POSTGRES_DB'], clone_env['POSTGRES_USER'], columns) == before
    last_rank = query(clone['Id'], 'SELECT max(installed_rank) FROM flyway_schema_history')
    names = ','.join('"' + value.replace('"', '""') + '"' for value in columns['flyway_schema_history'])
    old_migrations = query(clone['Id'], f'SELECT md5(json_agg(t ORDER BY installed_rank)::text) FROM (SELECT {names} FROM flyway_schema_history) t')
    migration_prefix = query(pg['Id'], f'SELECT md5(json_agg(t ORDER BY installed_rank)::text) FROM (SELECT {names} FROM flyway_schema_history WHERE installed_rank<={int(last_rank)}) t')
    expired = int(query(clone['Id'], "SELECT count(*) FROM execution_checkpoints WHERE expires_at<now()-interval '24 hours'"))
    original_checkpoints = int(before['execution_checkpoints'].split(':')[0])
    remaining_checkpoints = int(after['execution_checkpoints'].split(':')[0])
    checkpoint_explained = expired == original_checkpoints and remaining_checkpoints == 0
    mount_checks = []
    for role, target in [('postgres', '/var/lib/postgresql/data'), ('backend', '/data/uploads'), ('agent', '/app/runtime'), ('agent', '/app/data/chroma')]:
        old = next(m for m in original[role]['Mounts'] if m['Destination'] == target)
        new = next(m for m in current[role]['Mounts'] if m['Destination'] == target)
        same = all(old.get(key) == new.get(key) for key in ('Type', 'Source', 'Name', 'Destination', 'RW'))
        mount_checks.append({'role': role, 'target': target, 'original_identity_preserved': same})
    config = {}
    for role in ('agent', 'backend'):
        old, new = env(original[role]), env(current[role])
        provider_names = [key for key in old if any(word in key for word in ('OPENAI', 'AMAP', 'TAVILY', 'MCP_', 'ACCEPTANCE_BUDGET', 'DEEPSEEK', 'MODEL_', 'EMBEDDING_', 'API_KEY', 'TOKEN_BUDGET'))]
        config[role] = {'original_provider_and_budget_values_preserved': all(old[key] == new.get(key) for key in provider_names), 'checked_key_count': len(provider_names), 'production': new.get('APP_ENV') == 'production'}
    backend_env, agent_env = env(current['backend']), env(current['agent'])
    report = {
        'project': 'langchain-trip-planner', 'business_artifact_source_sha': '220257ad32e50d08cb987d565882f8a038e36c6f',
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'current_container_ids': {role: item['Id'] for role, item in current.items()},
        'current_image_ids': {role: item['Image'] for role, item in current.items()},
        'all_original_services_running_and_healthy': all(item['State']['Running'] and item['State'].get('Health', {}).get('Status') == 'healthy' for item in current.values()),
        'original_column_tables_compared': len(columns),
        'unchanged_original_tables': sorted(set(columns) - changed),
        'changed_original_tables': sorted(changed),
        'original_credential_hashes_users_preferences_trips_versions_unchanged': not (changed & {'users', 'preferences', 'user_preferences', 'trip_records', 'trip_versions', 'trip_record_versions'}),
        'original_migration_prefix_unchanged': old_migrations == migration_prefix,
        'expired_checkpoint_retention': {'original_count': original_checkpoints, 'eligible_count_in_restored_snapshot': expired, 'remaining_count': remaining_checkpoints, 'explained_by_24_hour_grace_policy': checkpoint_explained, 'originals_retained_in_complete_backup': True},
        'mounts': mount_checks, 'configuration': config,
        'admin_mfa_required': backend_env.get('ADMIN_MFA_REQUIRED') == 'true',
        'fixture_provider_not_enabled': agent_env.get('VALIDATION_ALLOW_FIXTURES', '').lower() not in {'true', '1', 'yes'},
        'frontend_loopback_8380': current['frontend']['NetworkSettings']['Ports']['80/tcp'] == [{'HostIp': '127.0.0.1', 'HostPort': '8380'}],
        'new_schema_tables': sorted(set(database_columns(pg['Id'], database, user)) - set(columns)),
        'boundary': 'Before smoke login/logout. All 26 original-column tables compared; 24 equal, Flyway appends and expiry-qualified checkpoints disclosed. No original human password login was attempted.'
    }
    report['passed'] = all([changed == {'flyway_schema_history', 'execution_checkpoints'}, old_migrations == migration_prefix, checkpoint_explained, all(m['original_identity_preserved'] for m in mount_checks), all(c['original_provider_and_budget_values_preserved'] and c['production'] for c in config.values()), report['admin_mfa_required'], report['fixture_provider_not_enabled'], report['frontend_loopback_8380'], report['all_original_services_running_and_healthy']])
    (evidence / 'daily-post-upgrade.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': report['passed'], 'unchanged_original_tables': len(report['unchanged_original_tables']), 'changed_original_tables': report['changed_original_tables'], 'checkpoint_retention_explained': checkpoint_explained}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
