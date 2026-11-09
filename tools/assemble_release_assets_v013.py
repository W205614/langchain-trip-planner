"""Concatenate checksum-bound staging parts into this one draft Release."""
import hashlib,json,os,subprocess
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from source_wire_reconstruct_v013 import restore
REPO='W205614/langchain-trip-planner';RELEASE=439171676
SOURCE='46dca1d2e8e2a45995134bb0f0600ae924c6de2d'
PLAN_SHA='f5fce8c69d4342d5baaf7988e1399435a479507eff5c30fd61ec3e754aa639c6'
def api(path):return json.loads(subprocess.check_output(['gh','api',path]))
def digest(path):
    with path.open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def assets():
    result=[];page=1
    while True:
        rows=api(f'repos/{REPO}/releases/{RELEASE}/assets?per_page=100&page={page}');result+=rows
        if len(rows)<100:return {r['name']:r for r in result}
        page+=1
def check_release():
    release=api(f'repos/{REPO}/releases/{RELEASE}')
    assert release['draft']and not release['prerelease']and release['tag_name']=='v0.1.3'and release['target_commitish']==SOURCE
    ref=api(f'repos/{REPO}/git/ref/tags/v0.1.3')['object']
    if ref['type']=='tag':ref=api(f'repos/{REPO}/git/tags/{ref["sha"]}')['object']
    assert ref['type']=='commit'and ref['sha']==SOURCE
check_release();rows=assets();work=Path('release-transfer');work.mkdir()
def download(asset,path):
    with path.open('xb')as f:subprocess.run(['gh','api',f'repos/{REPO}/releases/assets/{asset["id"]}','-H','Accept: application/octet-stream'],stdout=f,check=True)
plan_path=work/'plan.json';plan_asset=rows['transfer-v013-plan-rebuild.json'];download(plan_asset,plan_path)
assert digest(plan_path)==PLAN_SHA and plan_asset['digest']=='sha256:'+PLAN_SHA
plan=json.loads(plan_path.read_bytes());assert plan['source']==SOURCE and plan['release_id']==RELEASE
assert set(plan['assets'])=={'application-images-v0.1.3.tar.gz','container-sources-v0.1.3.zip'}
parts=[]
for name,file in plan['assets'].items():
    assert file['name']==name
    for part in file['parts']if 'parts'in file else file['skeleton']['parts']:
        assert part['name'].startswith('transfer-v013-')and Path(part['name']).name==part['name']
        asset=rows[part['name']];assert asset['state']=='uploaded'and asset['size']==part['bytes']and asset['digest']=='sha256:'+part['sha256']
        parts.append((part,asset))
def get_part(row):
    part,asset=row;path=work/part['name'];download(asset,path)
    assert path.stat().st_size==part['bytes']and digest(path)==part['sha256'];return path.name
with ThreadPoolExecutor(max_workers=12)as pool:
    jobs=[pool.submit(get_part,row)for row in parts]
    for job in as_completed(jobs):job.result()
receipts={}
for name,file in plan['assets'].items():
    destination=work/name
    inline=file if 'parts'in file else file['skeleton']
    assembled=destination if 'parts'in file else work/inline['name']
    with assembled.open('xb')as output:
        for part in inline['parts']:
            with(work/part['name']).open('rb')as source:
                while block:=source.read(1024*1024):output.write(block)
    assert assembled.stat().st_size==inline['bytes']and digest(assembled)==inline['sha256']
    if 'skeleton'in file:
        recipe_file=work/'source-recipe.json';asset=rows[file['recipe']['name']];download(asset,recipe_file)
        assert digest(recipe_file)==file['recipe']['sha256']and asset['digest']=='sha256:'+file['recipe']['sha256']
        recipe=json.loads(recipe_file.read_bytes());assert recipe['sha256']==file['sha256']and recipe['bytes']==file['bytes']
        print(json.dumps(restore(assembled,recipe,destination)),flush=True)
    assert destination.stat().st_size==file['bytes']and digest(destination)==file['sha256']
    check_release();current=assets()
    if name in current:
        assert current[name]['state']=='uploaded'and current[name]['digest']=='sha256:'+file['sha256']
    else:subprocess.run(['gh','release','upload','v0.1.3',str(destination),'--repo',REPO],check=True)
    final=assets()[name];assert final['state']=='uploaded'and final['size']==file['bytes']and final['digest']=='sha256:'+file['sha256']
    receipts[name]={k:final[k]for k in ('id','name','size','digest','state')}
result={'passed':True,'source':SOURCE,'definition_sha':os.environ['GITHUB_SHA'],'plan_sha256':PLAN_SHA,'parts':len(parts),'assets':receipts,'rebuild':False,'published':False}
Path('release-transfer-receipt.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
