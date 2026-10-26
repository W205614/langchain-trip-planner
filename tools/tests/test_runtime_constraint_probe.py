"""Pure counterexamples. Every Docker call is mocked; no live lifecycle/HTTP."""
import ast
import copy
import json
import os
import unittest
from unittest.mock import patch

from tools import runtime_constraint_probe as probe


CID = 'a' * 64
IMAGE = 'sha256:' + 'b' * 64


def context():
    image = {'Id': IMAGE, 'Config': {'Cmd': list(probe.APPROVED_CMD), 'Entrypoint': None,
              'WorkingDir': '/app', 'Env': ['PATH=/usr/local/bin:/usr/bin:/bin'],
              'Labels': {'org.opencontainers.image.revision': 'c' * 40}}}
    container = {'Id': CID, 'Image': IMAGE, 'Config': {'User': 'appuser', 'Cmd': list(probe.APPROVED_CMD),
                 'Entrypoint': None, 'WorkingDir': '/app',
                 'Env': ['PATH=/usr/local/bin:/usr/bin:/bin'] + [key + '=' + value for key, value in probe.PATHS.items()]},
                 'State': {'Running': True, 'Restarting': False, 'StartedAt': '2026-10-26T00:00:00Z'},
                 'HostConfig': {'Privileged': False, 'ReadonlyRootfs': True, 'CapAdd': None,
                    'Memory': 2 * 1024 ** 3, 'NanoCpus': 1_000_000_000, 'PidsLimit': 256,
                    'PidMode': '', 'IpcMode': 'private', 'NetworkMode': 'isolated-project-network',
                    'Devices': [], 'DeviceRequests': None, 'PortBindings': {},
                    'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges:true'], 'Tmpfs': dict(probe.TMPFS)},
                 'Mounts': [{'Type': 'volume', 'Destination': value, 'RW': True}
                            for value in ('/app/runtime', '/app/data/chroma')]}
    return container, image


class RuntimeProbeGuards(unittest.TestCase):
    def test_embedded_container_code_has_valid_syntax_without_execution(self):
        ast.parse(probe.CONTAINER_PROBE)

    def test_local_creation_tags_and_borrowed_cleanup_are_rejected_before_docker(self):
        with patch.dict(os.environ, {'CI': '', 'GITHUB_ACTIONS': ''}), patch.object(probe, 'docker') as docker:
            for image, container, cleanup in ((IMAGE, None, False), ('image:latest', CID, False), (IMAGE, CID, True)):
                with self.assertRaises(RuntimeError):
                    probe.validate_mode(image, container, cleanup)
            docker.assert_not_called()

    def test_privilege_command_extra_write_mount_and_weak_tmpfs_are_rejected(self):
        original, image = context()
        changes = [lambda c: c['HostConfig'].update(Privileged=True),
                   lambda c: c['HostConfig'].update(ReadonlyRootfs=False),
                   lambda c: c['HostConfig'].update(CapAdd=['SYS_ADMIN']),
                   lambda c: c['HostConfig'].update(SecurityOpt=[]),
                   lambda c: c['Config'].update(Cmd=['python', '-c', 'pass']),
                   lambda c: c['Mounts'].append({'Destination': '/app', 'Type': 'bind', 'RW': True}),
                   lambda c: c['HostConfig']['Tmpfs'].update({'/tmp': 'rw,size=128m,uid=1000,gid=1000,mode=1777'})]
        probe.validate_inspect(original, image)
        for change in changes:
            altered = copy.deepcopy(original); change(altered)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                probe.validate_inspect(altered, image)

    def test_a_bare_passed_json_is_not_an_actual_runtime_receipt(self):
        container, image = context()
        with patch.object(probe, 'inspect_container', return_value=container), \
             patch.object(probe, 'inspect_image', return_value=image), \
             patch.object(probe, 'docker', return_value=json.dumps({'passed': True})):
            result = probe.collect_existing(CID)
        self.assertFalse(result['passed'])
        self.assertEqual(result['constraints'], {})

    def test_read_only_source_or_library_shadow_mounts_are_rejected(self):
        original, image = context()
        for target in ('/app', '/app/app', '/usr/lib/x86_64-linux-gnu/libc.so.6'):
            altered = copy.deepcopy(original)
            altered['Mounts'].append({'Destination': target, 'Type': 'bind', 'RW': False})
            with self.subTest(target=target), self.assertRaises(RuntimeError):
                probe.validate_inspect(altered, image)

    def test_host_namespaces_devices_ports_and_unbounded_or_excess_resources_are_rejected(self):
        original, image = context()
        alternatives = {'PidMode': ['host', 'container:other'], 'IpcMode': ['host', 'container:other'],
                        'NetworkMode': ['host', 'container:other'], 'Devices': [[{'PathOnHost': '/dev/fuse'}]],
                        'DeviceRequests': [[{'Count': -1}]], 'PortBindings': [{'9000/tcp': [{'HostPort': '9000'}]}],
                        'Memory': [0, -1, 2 * 1024 ** 3 + 1], 'NanoCpus': [0, 2_000_000_001],
                        'PidsLimit': [0, -1, 257],
                        'SecurityOpt': [['no-new-privileges:true', 'seccomp=unconfined'],
                                        ['no-new-privileges:true', 'apparmor=unconfined']]}
        for field, values in alternatives.items():
            for value in values:
                altered = copy.deepcopy(original); altered['HostConfig'][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(RuntimeError):
                    probe.validate_inspect(altered, image)
        # Approved two-CPU CI and one-CPU canonical combinations both remain valid.
        original['HostConfig']['NanoCpus'] = 2_000_000_000
        original['HostConfig']['NetworkMode'] = 'none'
        observed = probe.validate_inspect(original, image)
        self.assertEqual(observed['resource_limits']['NanoCpus'], 2_000_000_000)

    def test_failed_proc_runtime_never_becomes_passed_when_inspect_flags_are_good(self):
        container, image = context()
        with patch.object(probe, 'inspect_container', return_value=container), \
             patch.object(probe, 'inspect_image', return_value=image), \
             patch.object(probe, 'docker', return_value=json.dumps({'passed': False, 'checks': [{'name': 'process', 'passed': False}]})):
            self.assertFalse(probe.collect_existing(CID)['passed'])

    def test_cwd_path_and_loader_overrides_cannot_substitute_rw_application_or_native_code(self):
        original, image = context()
        changed_cwd = copy.deepcopy(original); changed_cwd['Config']['WorkingDir'] = '/app/runtime'
        with self.assertRaises(RuntimeError):
            probe.validate_inspect(changed_cwd, image)
        for variable in probe.LOADER_VARIABLES:
            altered = copy.deepcopy(original)
            altered['Config']['Env'].append(variable + '=/app/runtime/private-override')
            with self.subTest(variable=variable), self.assertRaises(RuntimeError):
                probe.validate_inspect(altered, image)
        altered = copy.deepcopy(original)
        altered['Config']['Env'] = [entry for entry in altered['Config']['Env'] if not entry.startswith('PATH=')]
        altered['Config']['Env'].append('PATH=/app/runtime:/usr/local/bin:/usr/bin:/bin')
        with self.assertRaises(RuntimeError):
            probe.validate_inspect(altered, image)
        observed = probe.validate_inspect(original, image)
        self.assertTrue(observed['loader_environment_clear'])
        self.assertNotIn('PATH', observed['path_environment'])
        self.assertNotIn('private-override', json.dumps(observed))

    def test_cleanup_refuses_an_unowned_container_before_any_mutation(self):
        identity = 'd' * 32
        resources = {'schema_version': 1, 'owner_tool': 'trip-runtime-constraint-probe', 'borrowed_container': False,
                     'uuid': identity, 'image_id': IMAGE, 'container': {'id': CID}, 'volumes': []}
        unowned = {'Id': CID, 'Image': IMAGE, 'Name': '/daily-agent', 'Config': {'Labels': {}}}
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}), \
             patch.object(probe, 'inspect_container', return_value=unowned), patch.object(probe, 'docker') as docker:
            with self.assertRaises(RuntimeError):
                probe.cleanup_owned(resources)
            docker.assert_not_called()

    def test_cleanup_requires_github_actions_even_when_general_ci_is_true(self):
        with patch.dict(os.environ, {'CI': 'true', 'GITHUB_ACTIONS': ''}), patch.object(probe, 'docker') as docker:
            with self.assertRaises(RuntimeError):
                probe.cleanup_owned({})
            docker.assert_not_called()


if __name__ == '__main__':
    unittest.main()
