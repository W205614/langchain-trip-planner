import copy
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import unittest

from tools.windows_backup_recovery import execute_backup, restore_original


def containers():
    return {role: {'Id': role * 16, 'Image': 'approved-' + role, 'Mounts': [{'Source': role}],
                   'Config': {'Env': ['POSTGRES_USER=trip'], 'Labels': {'com.docker.compose.project': 'trip', 'com.docker.compose.service': role,
                                         'com.docker.compose.oneoff': 'false'}},
                   'State': {'Running': True}} for role in ('postgres', 'backend', 'agent', 'frontend')}


class RecoveryTests(unittest.TestCase):
    def test_failed_dependency_restores_exact_ids_in_order_without_starting_originally_stopped(self):
        original = containers(); original['agent']['State']['Running'] = False
        current = copy.deepcopy(original)
        for item in current.values(): item['State']['Running'] = False
        calls = []
        def docker(*args):
            self.assertEqual(args[0], 'start'); calls.append(args[1])
            next(x for x in current.values() if x['Id'] == args[1])['State']['Running'] = True
        result = restore_original(SimpleNamespace(docker=docker), original, 'trip',
            inspect=lambda identity: next(x for x in current.values() if x['Id'] == identity), wait_ready=lambda *_: None)
        self.assertTrue(result['passed'])
        self.assertEqual(calls, [original[x]['Id'] for x in ('postgres', 'backend', 'frontend')])

    def test_changed_mount_or_project_aborts_before_any_start_and_pg_failure_blocks_dependents(self):
        original = containers()
        for field in ('mount', 'project'):
            current = copy.deepcopy(original)
            if field == 'mount': current['frontend']['Mounts'] = [{'Source': 'foreign'}]
            else: current['frontend']['Config']['Labels']['com.docker.compose.project'] = 'foreign'
            calls=[]
            result=restore_original(SimpleNamespace(docker=lambda *a:calls.append(a)), original, 'trip',
                inspect=lambda identity: next(x for x in current.values() if x['Id'] == identity), wait_ready=lambda *_: None)
            self.assertFalse(result['passed']); self.assertEqual(calls, [])
        current=copy.deepcopy(original)
        for item in current.values(): item['State']['Running']=False
        calls=[]
        def fail(*args): calls.append(args); raise RuntimeError('daemon unavailable')
        result=restore_original(SimpleNamespace(docker=fail),original,'trip',
            inspect=lambda identity:next(x for x in current.values() if x['Id']==identity),wait_ready=lambda *_:None)
        self.assertFalse(result['passed']); self.assertEqual(calls,[('start',original['postgres']['Id'])])

    def test_failed_backup_stays_failed_and_compensation_remains_inside_lock(self):
        original=containers(); active=[]
        class Failure(RuntimeError):
            def __init__(self): self.report={'execution_started':True,'passed':False,'errors':['original failure']}
        @contextmanager
        def lock(_):
            active.append(True)
            try:yield
            finally:active.pop()
        def operation(*_):self.assertTrue(active);raise Failure()
        def docker(*args):
            self.assertTrue(active);self.assertEqual(args[0],'inspect')
            import json
            return json.dumps([next(x for x in original.values() if x['Id']==args[1])])
        maintenance=SimpleNamespace(maintenance_lock=lock,services=lambda _:original,_run_backup_locked=operation,
            MaintenanceFailure=Failure,docker=docker,_container_probe=lambda *_:(200,{}))
        # PostgreSQL probe uses exec; mock that precise read-only dependency probe.
        real_docker=maintenance.docker
        maintenance.docker=lambda *args:b'' if args[0]=='exec' else real_docker(*args)
        with self.assertRaises(Failure) as caught:execute_backup(maintenance,'trip',Path('unused'),execute=True)
        self.assertFalse(caught.exception.report['passed'])
        self.assertTrue(caught.exception.report['failure_recovery']['passed'])
        self.assertEqual(caught.exception.report['errors'],['original failure'])
        self.assertEqual(active,[])


if __name__ == '__main__': unittest.main()
