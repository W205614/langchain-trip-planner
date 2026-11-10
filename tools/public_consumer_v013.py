"""Read-only anonymous acquisition of this immutable public release."""
import hashlib, io, json, os, subprocess, urllib.request, zipfile
from pathlib import Path
from container_release import CONFIG_FIELDS

SOURCE='46dca1d2e8e2a45995134bb0f0600ae924c6de2d'
EXPECTED={'application-images-v0.1.3.tar.gz':'fb1b0cca49dc66ac7a35b721ecfdf5cc89d28ab79ade54e0262112b70a1fcb51','container-manifest.json':'5315d56afca4e1884f101cc1b7afe8aebe124b22b563f3c3cf5eeea6c6ed1a99','container-sources-v0.1.3.zip':'2ed36ed1386bc27cbae0587f23b71b48ca9903ddafd35cb918db4a7b11cbbf4b','SHA256SUMS':'e7291d408f4fccba05c80bd3f6ba4055779ecb53bb9828236313977eac3cc4b7'}
DIGESTS={'agent':'sha256:5e677a1ecf078c5c04ea73c629e42a7501b1e2ee1717aa544d3a5c7b58cec034','backend':'sha256:b9f07140c1538d3534c8c4add05cae7a84c28558e134c1d56626214ab23232f5','frontend':'sha256:fd2061e9737215934fa008ccd7f7c179d8b027e46010172ce54d915ef9317151'}
out=Path('consumer-evidence');out.mkdir();downloads=Path('consumer-downloads');downloads.mkdir()
def request(url):return urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'trip-anonymous-consumer'}),timeout=120)
def sha(path):
    with path.open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(name,value):(out/name).write_text(json.dumps(value,indent=2)+'\n')
with request('https://api.github.com/repos/W205614/langchain-trip-planner/releases/tags/v0.1.3')as response:release=json.load(response)
assert not release['draft']and not release['prerelease']and release['target_commitish']==SOURCE
assert {a['name']for a in release['assets']}==set(EXPECTED)
for asset in release['assets']:
    name=asset['name'];target=downloads/name
    assert asset['digest']=='sha256:'+EXPECTED[name]
    with request(asset['browser_download_url'])as response,target.open('xb')as f:
        while block:=response.read(1024*1024):f.write(block)
    assert target.stat().st_size==asset['size']and sha(target)==EXPECTED[name]
    print('Anonymous full asset hash verified: '+name,flush=True)
manifest=json.loads((downloads/'container-manifest.json').read_bytes());assert manifest['source']==SOURCE
with zipfile.ZipFile(downloads/'container-sources-v0.1.3.zip')as archive:
    assert archive.testzip()is None
    for line in archive.read('SHA256SUMS').decode().splitlines():
        digest,name=line.split(maxsplit=1);assert hashlib.sha256(archive.read(name)).hexdigest()==digest,name
    source_manifest=json.loads(archive.read('manifest.json'))
    for service in DIGESTS:assert source_manifest['images'][service]['layers']==manifest['images'][service]['base_layers']
with request('https://api.github.com/repos/W205614/langchain-trip-planner/zipball/refs/tags/v0.1.3')as response:raw=response.read()
tag_object=subprocess.check_output(['git','rev-parse','refs/tags/v0.1.3'],text=True).strip()
assert subprocess.check_output(['git','rev-parse','v0.1.3^{}'],text=True).strip()==SOURCE
with zipfile.ZipFile(io.BytesIO(raw))as archive:
    roots={n.split('/')[0]for n in archive.namelist()};assert len(roots)==1 and next(iter(roots)).endswith('-'+tag_object[:7])
    files={n.split('/',1)[1]:archive.read(n)for n in archive.namelist()if not n.endswith('/')}
local=subprocess.check_output(['git','-c','core.autocrlf=false','archive','--format=zip',SOURCE])
with zipfile.ZipFile(io.BytesIO(local))as archive:expected={n:archive.read(n)for n in archive.namelist()if not n.endswith('/')}
assert files==expected
for path in files:
    assert not path.startswith(('.git/','.codex-','backups/','deploy/runtime/','backend/data/chroma/','backend/data/knowledge_uploads/','backend/data/agent-runtime/'))
    assert path not in ('AGENTS.md','backend/.env','frontend/.env','.env')and '/node_modules/'not in path
save('source-archive-verification.json',{'passed':True,'tag':'v0.1.3','commit':SOURCE,'public_archive_downloaded':True,'source_files':len(files),'archive_root_uses_annotated_tag_object':tag_object,'tag_dereferenced_to_commit':SOURCE,'comparison_uses_canonical_git_line_endings':True,'all_file_contents_match_commit':True,'private_paths_present':False,'license_and_third_party_notices_present':{'LICENSE','THIRD_PARTY_NOTICES.md'}<=set(files),'zip_bytes':len(raw),'zip_sha256':hashlib.sha256(raw).hexdigest(),'boundary':'Anonymous GitHub-hosted consumer; local known-secret scan is performed separately against the identical Git archive contents'})
config=Path('consumer-docker');config.mkdir();(config/'config.json').write_text('{"auths":{}}\n');prefix=['docker','--config',str(config.resolve())]
images={}
for service,digest in DIGESTS.items():
    reference=f'ghcr.io/w205614/langchain-trip-planner-{service}@{digest}'
    subprocess.run(prefix+['pull',reference],check=True)
    actual=json.loads(subprocess.check_output(prefix+['image','inspect',reference]))[0]
    assert actual['RootFS']['Layers']==manifest['images'][service]['layers']
    assert {k:actual['Config'].get(k)or None for k in CONFIG_FIELDS}==manifest['images'][service]['configuration']
    registry=json.loads(subprocess.check_output(prefix+['buildx','imagetools','inspect',reference,'--raw']))
    assert registry['config']['digest']==manifest['images'][service]['config_digest']
    if service=='agent':command=['python','-c',"from pathlib import Path;print(Path('/licenses/SOURCE-ACCESS.txt').read_text());import app.agent_api.main;print('Agent import OK')"]
    elif service=='backend':command=['java','-version']
    else:command=['/docker-entrypoint.sh','nginx','-t']
    subprocess.run(prefix+['run','--rm','--network','none','--add-host','backend:127.0.0.1','--entrypoint',command[0],reference,*command[1:]],check=True)
    images[service]={'reference':reference,'image_id':actual['Id'],'layers_and_configuration_match':True,'registry_config_digest_verified':True,'anonymous_pull':True,'runtime_command_passed':True}
save('public-acquisition.json',{'passed':True,'source':SOURCE,'version':'v0.1.3','anonymous_release_assets_downloaded':True,'all_outer_and_inner_hashes_verified':True,'source_downloads':len(source_manifest['downloads']),'images':images,'assets':EXPECTED,'location':'GitHub-hosted clean Linux consumer; no registry or asset authorization header','boundary':'Same images as isolated candidate core-flow acceptance; no model/map calls or deployments'})
