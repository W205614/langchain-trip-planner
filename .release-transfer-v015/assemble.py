"""Upload only the same reviewed source ZIP to one existing unpublished draft."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.request import Request,urlopen
from source_span_restore import digest,restore

REPO='W205614/langchain-trip-planner'
SOURCE='7cceca80eb8524b9fbef34a385796224426de81e'
RELEASE=442450824
TAG='v0.1.5'
RECIPE_SHA='3e7a0db2158f7c1d3c29a6fc41aa42a6c1079ac961cb94f3eb5041047a77c93c'
SKELETON_SHA='8796b3af29a3bbb806317f2db6f15056fbeb2685f78cdccace21de459e6cbfa2'
NOTES_SHA='1c17215dcf5c66b568f7bf1a0dd7b5521a02390bb9560635e283a557536d8334'
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
assert recipe['sha256']=='93c5c7f7abec550f18b3a8236778ce939c6bc8c076cff7531956568b74ecd8ff'
assert recipe['bytes']==1392486212
name='container-sources-v0.1.5.zip'
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
