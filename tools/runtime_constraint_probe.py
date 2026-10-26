"""Bind actual Agent runtime constraints to one immutable image and container.

Local --container only inspects/executes that existing container; no lifecycle.
CI without --container creates UUID-owned, network-none objects and retains them
running by default so the gate can independently recheck the receipt. Explicit
CI cleanup removes only those exact owned objects; a removed container cannot
subsequently satisfy the live runtime gate. No model/RAG/map/PG calls are made.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from uuid import uuid4

LABEL = 'com.tripplanner.runtime-proof'
APPROVED_CMD = ['uvicorn', 'app.agent_api.main:app', '--host', '0.0.0.0', '--port', '9000', '--workers', '1']
PATHS = {'DATA_DIR': '/app/runtime', 'CHROMA_DIR': '/app/data/chroma', 'LOG_DIR': '/app/logs',
         'PYTHONDONTWRITEBYTECODE': '1', 'XDG_CACHE_HOME': '/tmp/cache'}
TMPFS = {'/tmp': 'rw,nosuid,nodev,noexec,size=128m,uid=1000,gid=1000,mode=1777',
         '/app/logs': 'rw,nosuid,nodev,noexec,size=64m,uid=1000,gid=1000,mode=0750'}
LOADER_VARIABLES = ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP')


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def docker(*args, timeout=90):
    result = subprocess.run(['docker', *map(str, args)], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout)
    # Never print command arguments, environment values or daemon stderr.
    require(result.returncode == 0, 'Docker operation failed: ' + args[0])
    return result.stdout


def inspect_container(identity):
    require(bool(re.fullmatch('[0-9a-f]{64}', identity or '')), 'Full immutable container ID required')
    value = json.loads(docker('container', 'inspect', identity))[0]
    require(value.get('Id') == identity, 'Container inspect identity mismatch')
    return value


def inspect_image(identity):
    require(bool(re.fullmatch('sha256:[0-9a-f]{64}', identity or '')), 'Immutable sha256 image ID required; tags are refused')
    value = json.loads(docker('image', 'inspect', identity))[0]
    require(value.get('Id') == identity, 'Image inspect identity mismatch')
    return value


def size_bytes(value):
    match = re.fullmatch(r'(\d+)([bkmg]?)(?:b)?', value.lower())
    require(bool(match), 'Unsupported tmpfs size')
    return int(match[1]) * {'': 1, 'b': 1, 'k': 1024, 'm': 1024 ** 2, 'g': 1024 ** 3}[match[2]]


def validate_inspect(container, image):
    host, config, state = container['HostConfig'], container['Config'], container['State']
    require(container['Image'] == image['Id'], 'Container does not use the inspected exact image')
    require(state.get('Running') is True and not state.get('Restarting'), 'Actual service must be running without restart in progress')
    require(host.get('Privileged') is False and host.get('ReadonlyRootfs') is True, 'Privileged or writable-root runtime refused')
    require(not host.get('CapAdd') and set(host.get('CapDrop') or []) == {'ALL'}, 'Require only cap-drop ALL and no added capabilities')
    security_options = {'no-new-privileges:true' if value == 'no-new-privileges' else value
                        for value in host.get('SecurityOpt') or []}
    require(security_options == {'no-new-privileges:true'}, 'Require approved no-new-privileges without security-profile overrides')
    require(host.get('PidMode') in (None, '', 'private') and host.get('IpcMode') in (None, '', 'private'),
            'Host/shared PID or IPC namespace refused')
    network_mode = host.get('NetworkMode') or ''
    require(network_mode != 'host' and not network_mode.startswith('container:'), 'Host/shared network namespace refused')
    require(not host.get('Devices') and not host.get('DeviceRequests') and not host.get('DeviceCgroupRules'),
            'Device mappings, requests or cgroup device rules refused')
    require(not host.get('PortBindings'), 'Agent host port publication refused')
    for field, maximum in (('Memory', 2 * 1024 ** 3), ('NanoCpus', 2_000_000_000), ('PidsLimit', 256)):
        value = host.get(field)
        require(type(value) is int and 0 < value <= maximum, f'{field} must be positive and within the approved resource limit')
    require(config.get('User') in ('appuser', '1000', '1000:1000'), 'Approved Agent user required')
    require(config.get('Cmd') == image['Config'].get('Cmd') == APPROVED_CMD, 'Require real image-default uvicorn Agent command')
    require(not config.get('Entrypoint') and not image['Config'].get('Entrypoint'), 'Unexpected entrypoint override')
    require(config.get('WorkingDir') == image['Config'].get('WorkingDir') == '/app', 'Actual and image-default working directories must both be /app')
    env = dict(value.split('=', 1) for value in config.get('Env', []) if '=' in value)
    image_env = dict(value.split('=', 1) for value in image['Config'].get('Env', []) if '=' in value)
    require(bool(image_env.get('PATH')) and env.get('PATH') == image_env['PATH'], 'Actual PATH must exactly match the immutable image default')
    require(not any(env.get(key) for key in LOADER_VARIABLES), 'Nonempty native/Python loader environment override refused')
    require(all(env.get(key) == value for key, value in PATHS.items()), 'Approved writable/cache paths or bytecode flag differ')
    destinations = [item['Destination'] for item in container.get('Mounts', [])]
    require(set(destinations) <= {'/app/runtime', '/app/data/chroma', '/tmp', '/app/logs'} and
            len(destinations) == len(set(destinations)), 'Unapproved or duplicate mount, including read-only source/library overlays')
    mounted = {item['Destination']: item for item in container.get('Mounts', [])}
    for target in ('/app/runtime', '/app/data/chroma'):
        require(target in mounted and mounted[target].get('RW') is True and mounted[target].get('Type') in ('bind', 'volume'), 'Required data mount missing or read-only')
    configured = host.get('Tmpfs') or {}
    require(set(configured) == set(TMPFS), 'Require exactly the approved tmpfs destinations')
    for target, expected in TMPFS.items():
        actual = configured[target].split(',')
        desired = expected.split(',')
        options = dict(item.split('=', 1) for item in actual if '=' in item)
        wanted = dict(item.split('=', 1) for item in desired if '=' in item)
        require({'rw', 'nosuid', 'nodev', 'noexec'} <= set(actual), 'tmpfs security flags differ')
        require(options.get('uid') == options.get('gid') == '1000', 'tmpfs ownership differs')
        require(int(options.get('mode', '0'), 8) == int(wanted['mode'], 8) and
                size_bytes(options.get('size', '0')) == size_bytes(wanted['size']), 'tmpfs mode or size differs')
    return {'container_id': container['Id'], 'image_id': container['Image'],
            'started_at': state.get('StartedAt'), 'running': state['Running'], 'user': config['User'],
            'cmd': config['Cmd'], 'entrypoint': config.get('Entrypoint'),
            'working_directory_is_image_default_app': True, 'path_matches_image_default': True,
            'loader_environment_clear': True, 'loader_variable_names_checked': list(LOADER_VARIABLES),
            'privileged': host['Privileged'], 'readonly_rootfs': host['ReadonlyRootfs'],
            'cap_add': host.get('CapAdd'), 'cap_drop': host['CapDrop'], 'security_opt': host['SecurityOpt'],
            'namespaces': {key: host.get(key) for key in ('PidMode', 'IpcMode', 'NetworkMode')},
            'devices': host.get('Devices'), 'device_requests': host.get('DeviceRequests'),
            'device_cgroup_rules': host.get('DeviceCgroupRules'), 'port_bindings': host.get('PortBindings'),
            'resource_limits': {key: host[key] for key in ('Memory', 'NanoCpus', 'PidsLimit')},
            'tmpfs': configured, 'path_environment': {key: env.get(key) for key in PATHS},
            'mounts': [{key: item.get(key) for key in ('Type', 'Name', 'Destination', 'RW', 'Propagation')}
                       for item in container.get('Mounts', [])]}


# Only stdlib, reviewed tool-absence checker and literal loopback readiness.
# Executed as the container's configured user, not with a root override.
CONTAINER_PROBE = r'''
import hashlib,json,os,pathlib,re,stat,subprocess,sys,urllib.request,uuid
def need(x,m):
 if not x:raise RuntimeError(m)
def status(pid):
 out={}
 for line in pathlib.Path('/proc/'+str(pid)+'/status').read_text().splitlines():
  k,_,v=line.partition(':')
  if k in ('Uid','Gid','CapEff','CapBnd','NoNewPrivs','Seccomp'):out[k]=v.strip()
 return out
def mounts():
 out=[]
 for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines():
  left,right=line.split(' - ',1); f=left.split(); r=right.split()
  target=re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),f[4])
  out.append({'target':target,'options':f[5].split(','),'filesystem':r[0],'super_options':r[2].split(',')})
 return out
report={'passed':False,'checks':[],'data':{}}
def check(name,fn):
 try:report['data'][name]=fn();report['checks'].append({'name':name,'passed':True})
 except Exception as e:report['checks'].append({'name':name,'passed':False,'error_type':type(e).__name__,'message':str(e)[:300]})
def process():
 command=[x.decode() for x in pathlib.Path('/proc/1/cmdline').read_bytes().split(b'\0') if x]
 need(any(pathlib.Path(x).name=='uvicorn' for x in command) and 'app.agent_api.main:app' in command,'PID 1 is not actual production Agent uvicorn')
 one,own=status(1),status('self')
 for value in (one,own):
  need(int(value['CapEff'],16)==int(value['CapBnd'],16)==0,'Nonzero actual capabilities')
  need(value['NoNewPrivs']=='1','Actual no-new-privileges absent')
  need(value['Seccomp']=='2','Actual default syscall filtering absent')
 need(os.geteuid()==int(one['Uid'].split()[1])==1000,'Probe/Agent actual UID must both be 1000')
 return {'pid1_cmdline':command,'pid1_status':one,'self_status':own,'self_uid':os.geteuid()}
def root():
 value=next(x for x in mounts() if x['target']=='/');need('ro' in value['options'],'Actual root mount is writable');return value
def writepaths():
 records=[];ident=uuid.uuid4().hex
 for folder in ('/app/runtime','/app/data/chroma','/tmp','/app/logs'):
  p=pathlib.Path(folder);need(p.is_dir(),'Missing required write directory')
  st=p.stat();need(st.st_uid==st.st_gid==1000,'Write directory not owned by actual app UID/GID')
  mount=max((x for x in mounts() if folder==x['target'] or folder.startswith(x['target'].rstrip('/')+'/')),key=lambda x:len(x['target']))
  need(mount['target']==folder and 'rw' in mount['options'],'Required dedicated writable mount absent')
  if folder in ('/tmp','/app/logs'):
   need(mount['filesystem']=='tmpfs' and {'rw','nosuid','nodev','noexec'}<=set(mount['options']),'Actual tmpfs security flags absent')
   need(stat.S_IMODE(st.st_mode)==(0o1777 if folder=='/tmp' else 0o750),'Actual tmpfs mode differs')
  payload=('runtime-constraint-canary:'+ident).encode();target=p/('.runtime-constraint-canary-'+ident)
  fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'wb') as out:out.write(payload);out.flush();os.fsync(out.fileno())
  need(target.read_bytes()==payload,'Actual write/readback differs')
  records.append({'path':folder,'file':str(target),'readback':True,'sha256':hashlib.sha256(payload).hexdigest(),'uid':st.st_uid,'gid':st.st_gid,'mode':oct(stat.S_IMODE(st.st_mode)),'mount':mount})
 return records
def tools():
 manifest='/usr/local/share/tripplanner/restricted-runtime-tools.json'
 r=subprocess.run([sys.executable,'/app/scripts/restrict_runtime_tools.py','--check','--manifest',manifest],capture_output=True,text=True,timeout=60)
 data=json.loads(r.stdout);need(r.returncode==0 and data.get('status')=='verified_absent','Restricted tools aliases/copies/filecaps not verified absent')
 return {'status':data['status'],'manifest_sha256':hashlib.sha256(pathlib.Path(manifest).read_bytes()).hexdigest(),'result':data}
def ready():
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args,**kwargs):raise RuntimeError('Readiness redirected')
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 with opener.open('http://127.0.0.1:9000/readyz',timeout=5) as response:
  need(response.status==200,'Actual readiness not 200');raw=response.read(16385);need(len(raw)<=16384,'Readiness body too large');data=json.loads(raw)
 need(data.get('status')=='ready' and data.get('service')=='trip-agent','Wrong actual readiness service')
 return {'status':200,'service':'trip-agent','body_sha256':hashlib.sha256(raw).hexdigest()}
for name,fn in [('process',process),('root_mount',root),('writepaths',writepaths),('restricted_tools',tools),('readiness',ready)]:check(name,fn)
report['passed']=all(x['passed'] for x in report['checks']);print(json.dumps(report))
'''


def collect_existing(container_id):
    """Fresh inspection/exec only; retain canaries, never lifecycle or cleanup."""
    proof = {'schema_version': 1, 'probe_source_sha256': digest(Path(__file__).read_bytes().replace(b'\r\n', b'\n')),
             'container_id': container_id, 'passed': False, 'constraints': {}, 'data': {}}
    try:
        container = inspect_container(container_id)
        image = inspect_image(container['Image'])
        proof['image_id'] = container['Image']
        revision = image['Config'].get('Labels', {}).get('org.opencontainers.image.revision', '')
        require(bool(re.fullmatch('[0-9a-f]{40}', revision)), 'Exact source revision missing from image OCI label')
        proof['source_revision'] = revision
        view = validate_inspect(container, image)
        proof['data']['inspect'] = view
        proof['data']['inspect_digest'] = digest(canonical(view))
        proof['data']['source_image'] = {'id': image['Id'], 'revision': revision, 'default_cmd': image['Config']['Cmd']}
        runtime = json.loads(docker('exec', container_id, 'python', '-c', CONTAINER_PROBE, timeout=100))
        proof['data']['runtime'] = runtime
        require(runtime.get('passed') is True, 'Actual proc/mount/writepath/tool/readiness runtime check failed')
        # Reinspect identity/state after exec; a lifecycle change invalidates the receipt.
        after = inspect_container(container_id)
        require(after['Image'] == container['Image'] and after['State'].get('Running') is True and
                after['State'].get('StartedAt') == container['State'].get('StartedAt'), 'Container changed during runtime observation')
        values = runtime['data']['process']['pid1_status']
        proof['constraints'] = {'user': str(runtime['data']['process']['self_uid']),
            'cap_eff': values['CapEff'], 'cap_bnd': values['CapBnd'], 'no_new_privileges': values['NoNewPrivs'] == '1',
            'readonly_rootfs': 'ro' in runtime['data']['root_mount']['options'], 'privileged': view['privileged'],
            'cap_drop_all': set(view['cap_drop']) == {'ALL'},
            'restricted_tools_absent': runtime['data']['restricted_tools']['status'] == 'verified_absent', 'actual_uvicorn': True}
        proof['passed'] = True
    except Exception as error:
        proof['error'] = {'type': type(error).__name__, 'message': str(error)[:500]}
    proof['checked_at'] = datetime.now(timezone.utc).isoformat()
    return proof


def validate_mode(image, container, cleanup):
    require(bool(re.fullmatch('sha256:[0-9a-f]{64}', image or '')), 'Immutable sha256 image ID required')
    if container:
        require(bool(re.fullmatch('[0-9a-f]{64}', container)), 'Full existing container ID required')
        require(not cleanup, 'Borrowed container cleanup is forbidden')
    else:
        require(os.environ.get('CI', '').lower() in ('true', '1'), 'Creating runtime fixture objects is CI-only; local runs require --container')


def volume_identity(value):
    return {key: value.get(key) for key in ('Name', 'CreatedAt', 'Driver', 'Scope', 'Mountpoint', 'Labels')}


def cleanup_owned(resources, save=lambda: None):
    """CI-only exact UUID/name/label/container-ID/volume-fingerprint cleanup."""
    require(os.environ.get('GITHUB_ACTIONS', '').lower() == 'true', 'Cleanup requires GITHUB_ACTIONS=true')
    require(resources.get('schema_version') == 1 and resources.get('owner_tool') == 'trip-runtime-constraint-probe'
            and resources.get('borrowed_container') is False, 'Only this tool own-resource journal may be cleaned')
    identity = resources['uuid']
    require(bool(re.fullmatch('[0-9a-f]{32}', identity)), 'Malformed owned resource identity')
    expected = 'trip-runtime-proof-' + identity
    container = resources.get('container')
    if container and not container.get('removed'):
        value = inspect_container(container['id'])
        require(value.get('Name') == '/' + expected + '-agent' and value['Config'].get('Labels', {}).get(LABEL) == identity,
                'Refuse cleanup of a container without exact UUID name/label/ID')
        require(value['Image'] == resources['image_id'], 'Owned container image changed')
        docker('container', 'rm', '--force', container['id'])
        container['removed'] = True; save()
    for volume in reversed(resources.get('volumes', [])):
        if volume.get('removed'):
            continue
        require(volume['name'] in (expected + '-runtime', expected + '-chroma'), 'Refuse unrelated volume name')
        value = json.loads(docker('volume', 'inspect', volume['name']))[0]
        require(value.get('Labels', {}).get(LABEL) == identity and
                digest(canonical(volume_identity(value))) == volume['identity_digest'], 'Volume ownership/identity changed')
        docker('volume', 'rm', volume['name'])
        volume['removed'] = True; save()
    return {'passed': True, 'explicit_ci_only': True}


def create_owned(image, resources, save):
    validate_mode(image, None, False)
    identity = resources['uuid']; prefix = 'trip-runtime-proof-' + identity
    for suffix in ('runtime', 'chroma'):
        name = prefix + '-' + suffix
        resources['creation_in_progress'] = {'type': 'volume', 'name': name}; save()
        docker('volume', 'create', '--driver', 'local', '--label', LABEL + '=' + identity, name)
        value = json.loads(docker('volume', 'inspect', name))[0]
        resources['volumes'].append({'name': name, 'identity_digest': digest(canonical(volume_identity(value)))})
        resources.pop('creation_in_progress', None); save()
    # Default Docker empty-volume copy-up preserves image appuser-owned path
    # metadata. Actual UID ownership and write/readback must prove this worked;
    # no privileged/chown initializer is used as a fallback.
    args = ['container', 'create', '--pull=never', '--name', prefix + '-agent', '--label', LABEL + '=' + identity,
            '--network', 'none', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--read-only',
            '--memory', '2g', '--cpus', '2', '--pids-limit', '256']
    for target, options in TMPFS.items():
        args += ['--tmpfs', target + ':' + options]
    for suffix, target in (('runtime', '/app/runtime'), ('chroma', '/app/data/chroma')):
        args += ['--mount', f'type=volume,source={prefix}-{suffix},target={target}']
    env = {**PATHS, 'APP_ENV': 'validation', 'VALIDATION_ALLOW_FIXTURES': 'yes',
           'INTERNAL_SERVICE_KEY': 'runtime-proof-synthetic-internal-key-0000000000000000',
           'BUSINESS_URL': 'http://business.invalid', 'DATASET_ID': '', 'OUTBOUND_LEDGER_REQUIRED': 'true',
           'RAG_ENABLED': 'false', 'AMAP_API_KEY': '', 'LLM_API_KEY': '', 'EMBEDDING_API_KEY': ''}
    for key, value in env.items():
        args += ['--env', key + '=' + value]
    # No entrypoint/command override: run the real immutable image default.
    resources['creation_in_progress'] = {'type': 'container', 'name': prefix + '-agent'}; save()
    container_id = docker(*args, image).strip()
    require(bool(re.fullmatch('[0-9a-f]{64}', container_id)), 'Create did not return immutable container ID')
    resources['container'] = {'id': container_id, 'name': prefix + '-agent'}
    resources.pop('creation_in_progress', None); save()
    docker('container', 'start', container_id)
    for _ in range(60):
        value = inspect_container(container_id)
        require(value['State'].get('Running') is True, 'Own actual uvicorn fixture stopped before readiness')
        ready = subprocess.run(['docker', 'exec', container_id, 'python', '-c',
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000/readyz',timeout=2)"],
            capture_output=True, timeout=5)
        if ready.returncode == 0:
            return container_id
        time.sleep(.5)
    raise RuntimeError('Own actual uvicorn did not reach readiness')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image')
    parser.add_argument('--reports', type=Path)
    parser.add_argument('--container')
    parser.add_argument('--cleanup-owned-resources', action='store_true')
    parser.add_argument('--cleanup-journal', type=Path)
    args = parser.parse_args(argv)
    if args.cleanup_journal:
        require(args.cleanup_owned_resources and not args.container and not args.image and not args.reports,
                'Journal cleanup requires only --cleanup-journal FILE --cleanup-owned-resources')
        require(os.environ.get('GITHUB_ACTIONS', '').lower() == 'true', 'Journal cleanup requires GITHUB_ACTIONS=true')
        require(not args.cleanup_journal.is_symlink(), 'Symlink cleanup journal refused')
        if not args.cleanup_journal.is_file():
            match = re.fullmatch(r'runtime-resources-([0-9a-f]{32})\.json', args.cleanup_journal.name)
            require(bool(match), 'Missing journal must still identify an exact generated UUID')
            require(not docker('container', 'ls', '-aq', '--filter', 'label=' + LABEL + '=' + match[1]).strip() and
                    not docker('volume', 'ls', '-q', '--filter', 'label=' + LABEL + '=' + match[1]).strip(),
                    'Missing journal but UUID-owned objects exist; refuse unrecorded cleanup')
            print(json.dumps({'passed': True, 'cleanup': 'no_resources_for_missing_uuid_journal'})); return 0
        resources = json.loads(args.cleanup_journal.read_text(encoding='utf-8'))
        require(args.cleanup_journal.name == 'runtime-resources-' + resources.get('uuid', '') + '.json',
                'Cleanup filename must bind the exact tool UUID journal')
        def save_cleanup():
            args.cleanup_journal.write_text(json.dumps(resources, indent=2) + '\n', encoding='utf-8')
        try:
            resources['cleanup_result'] = cleanup_owned(resources, save_cleanup)
            require(not resources.get('creation_in_progress'), 'Recorded objects cleaned, but interrupted unjournaled creation requires review')
        except Exception as error:
            resources['cleanup_result'] = {'passed': False, 'error_type': type(error).__name__}; save_cleanup()
            raise
        save_cleanup(); print(json.dumps(resources['cleanup_result'])); return 0
    require(args.reports is not None, '--reports required for runtime observation')
    validate_mode(args.image, args.container, args.cleanup_owned_resources)
    if args.cleanup_owned_resources:
        require(os.environ.get('GITHUB_ACTIONS', '').lower() == 'true', 'Cleanup requires GITHUB_ACTIONS=true')
    identity = uuid4().hex
    args.reports.mkdir(parents=True, exist_ok=True)
    output = args.reports / ('runtime-proof-' + identity + '.json')
    resource_file = args.reports / ('runtime-resources-' + identity + '.json')
    resources = {'schema_version': 1, 'owner_tool': 'trip-runtime-constraint-probe', 'borrowed_container': False,
                 'uuid': identity, 'image_id': args.image, 'container': None, 'volumes': []}
    proof = {'schema_version': 1, 'passed': False, 'image_id': args.image}
    def write(path, value):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2); stream.write('\n')
    def save():
        write(resource_file, resources)
    try:
        if not args.container:
            save()  # Journal exists before any Docker mutation.
        inspect_image(args.image)  # Never pull or silently substitute a tag.
        container_id = args.container or create_owned(args.image, resources, save)
        proof = collect_existing(container_id)
        require(proof.get('image_id') == args.image, 'Borrowed container does not use the requested image')
    except Exception as error:
        proof['passed'] = False; proof['error'] = {'type': type(error).__name__, 'message': str(error)[:500]}
    finally:
        proof['resources'] = {'borrowed_container': bool(args.container), 'retained_by_default': not args.cleanup_owned_resources,
                              'journal': None if args.container else str(resource_file)}
        if args.cleanup_owned_resources:
            try:
                proof['cleanup'] = cleanup_owned(resources, save)
                require(not resources.get('creation_in_progress'), 'Recorded objects cleaned; unjournaled creation retained for review')
            except Exception as error:
                proof['passed'] = False; proof['cleanup'] = {'passed': False, 'error_type': type(error).__name__}
        write(output, proof)
    print(json.dumps({'passed': proof['passed'], 'runtime_proof': str(output), 'container_id': proof.get('container_id'),
                      'cleanup_journal': None if args.container else str(resource_file)}))
    return 0 if proof['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
