"""Restore exact ZIP bytes from an inline skeleton and checksum-bound data spans."""
import argparse,gzip,hashlib,json,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen
def digest(path):
    with path.open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def restore(skeleton,recipe,destination,cache=None):
    assert digest(skeleton)==recipe['skeleton_sha256']
    previous=0
    for row in recipe['segments']:
        assert previous<=row['offset']and row['offset']+row['bytes']<=recipe['bytes'];previous=row['offset']+row['bytes']
        assert row['url'].startswith('https://')
    total=0
    with gzip.open(skeleton,'rb')as source,destination.open('xb')as output:
        while block:=source.read(1024*1024):
            total+=len(block);assert total<=recipe['bytes'];output.write(block)
    assert total==recipe['bytes']
    def fill(row):
        for attempt in range(3):
            try:
                if cache is not None:
                    path=(cache/row['path']).resolve();assert path.is_relative_to(cache.resolve());source=path.open('rb')
                else:source=urlopen(Request(row['url'],headers={'User-Agent':'trip-source-transfer'}),timeout=120)
                count=0;hash=hashlib.sha256()
                with source,destination.open('r+b')as output:
                    output.seek(row['offset'])
                    while block:=source.read(1024*1024):
                        count+=len(block)
                        if count>row['bytes']:raise RuntimeError('Source exceeded its exact ZIP span')
                        hash.update(block);output.write(block)
                if count!=row['bytes']:raise IncompleteRead(b'',row['bytes']-count)
                if hash.hexdigest()!=row['sha256']:raise RuntimeError('Source digest changed: '+row['path'])
                return row['path']
            except HTTPError:raise
            except (URLError,TimeoutError,ConnectionError,IncompleteRead):
                if attempt==2:raise
                time.sleep(1)
    failures=[];passed=0
    with ThreadPoolExecutor(max_workers=12)as pool:
        jobs={pool.submit(fill,row):row['path']for row in recipe['segments']}
        for job in as_completed(jobs):
            try:job.result();passed+=1
            except Exception as error:failures.append({'path':jobs[job],'error':str(error),'type':type(error).__name__})
            if passed and passed%250==0:print('Verified source spans '+str(passed),flush=True)
    if failures:raise RuntimeError('Source span transfer incomplete: '+json.dumps(failures))
    assert destination.stat().st_size==recipe['bytes']and digest(destination)==recipe['sha256']
    return {'passed':True,'bytes':recipe['bytes'],'sha256':recipe['sha256'],'source_spans_verified':passed,'application_rebuild':False}
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--skeleton',type=Path,required=True);parser.add_argument('--recipe',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--cache',type=Path);args=parser.parse_args()
    print(json.dumps(restore(args.skeleton,json.loads(args.recipe.read_bytes()),args.output,args.cache)))
