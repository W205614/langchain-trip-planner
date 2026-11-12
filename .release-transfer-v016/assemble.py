"""Upload only the same reviewed source ZIP to one existing unpublished draft."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.request import Request,urlopen
from source_span_restore import digest,restore

REPO='W205614/langchain-trip-planner'
SOURCE='2648d33576c987294facc7b797bbcea74af2214e'
RELEASE=442818361
TAG='v0.1.6'
RECIPE_SHA='4c9f6b8bed1c07e44eab9d5b69f01e97102181683f3e61b371abc56d6a55996c'
SKELETON_SHA='439b7f9dbec92f5409c40479d65ecad275546988bdf5a13782648e6064bf4b23'
NOTES_SHA='4c9875cf8b3ed692c7b6e1f360fb7a3ded7865ac6620f2f695a1e66e6acea870'
here=Path(__file__).resolve().parent
def api(path):return json.loads(subprocess.check_output(['gh','api',path]))
def check():
    release=api(f'repos/{REPO}/releases/{RELEASE}')
    assert release['draft'] and not release['prerelease'] and release['tag_name']==TAG and release['target_commitish']==SOURCE
    assert hashlib.sha256(release['body'].encode()).hexdigest()==NOTES_SHA
    obj=api(f'repos/{REPO}/git/ref/tags/{TAG}')['object']
    if obj['type']=='tag':obj=api(f'repos/{REPO}/git/tags/{obj["sha"]}')['object']
    assert obj['type']=='commit' and obj['sha']==SOURCE
    assert api(f'repos/{REPO}/git/ref/heads/main')['object']['sha']==SOURCE
    return release
def assets():
    rows=api(f'repos/{REPO}/releases/{RELEASE}/assets?per_page=100')
    assert len(rows)<100 and len({r['name']for r in rows})==len(rows)
    return {r['name']:r for r in rows}
check()
recipe_file=here/'source-span-recipe.json';skeleton=here/'source-span-skeleton.gz'
assert digest(recipe_file)==RECIPE_SHA and digest(skeleton)==SKELETON_SHA
recipe=json.loads(recipe_file.read_bytes())
assert recipe['sha256']=='0fb1c5874c75eba5ae3b990160c9fa46b492a743304fd8bf173082c2cc2fd0ae'
assert recipe['bytes']==1392487767
name='container-sources-v0.1.6.zip'
def matches(row):return row['state']=='uploaded' and row['size']==recipe['bytes'] and row['digest']=='sha256:'+recipe['sha256']
current=assets()
if name not in current:
    base_asset=api(f'repos/{REPO}/releases/assets/{recipe["base"]["asset_id"]}')
    assert base_asset['state']=='uploaded' and base_asset['size']==recipe['base']['bytes'] and base_asset['digest']=='sha256:'+recipe['base']['sha256']
    assert base_asset['browser_download_url']==recipe['base']['url']
    base=here/'base-public-source.zip'
    with urlopen(Request(recipe['base']['url'],headers={'User-Agent':'trip-exact-source-span-transport'}),timeout=180)as response,base.open('xb')as stream:
        while block:=response.read(1024*1024):stream.write(block)
    result=restore(skeleton,recipe,base,here/name)
    assert result['passed']
    check();current=assets()
    if name not in current:
        subprocess.run(['gh','release','upload',TAG,str(here/name),'--repo',REPO],check=True)
    else:assert matches(current[name]),'Concurrent source object requires inspection; never overwrite'
else:assert matches(current[name]),'Incomplete/different source asset must be reviewed before this job'
final=assets()[name];assert matches(final)
Path('source-span-transfer-receipt.json').write_text(json.dumps({'passed':True,'source':SOURCE,'definition_sha':os.environ['GITHUB_SHA'],'release_id':RELEASE,'version':TAG,'asset':{k:final[k]for k in ('id','name','size','digest','state')},'rebuild':False,'published':False,'base_sha256':recipe['base']['sha256'],'spans':len(recipe['segments']),'recipe_sha256':RECIPE_SHA,'skeleton_sha256':SKELETON_SHA},indent=2)+'\n')
print(json.dumps({'passed':True,'asset_id':final['id'],'sha256':recipe['sha256'],'application_rebuilt':False}))
