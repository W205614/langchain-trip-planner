"""Run from the release checkout, after collection and before sealing.

Extract missing wheel notices from verified source releases. Also preserve
crate notices/sources conservatively from the native wheels' source Cargo.lock
sets, including build/dev entries; this is not an exact binary linkage claim.
"""
import argparse,hashlib,json,re,shutil,sys,tarfile,tomllib,zipfile
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
root=Path.cwd();sys.path.insert(0,str(root/'tools'))
from container_sources import Collector,checksum
parser=argparse.ArgumentParser();parser.add_argument('--sources',type=Path,required=True);parser.add_argument('--bundle',type=Path,required=True);args=parser.parse_args()
folder=args.sources.resolve();metadata=json.loads((folder/'manifest.json').read_bytes());collector=Collector(folder);collector.downloads=metadata['downloads']
records=[];generated={};crates={};git_sources={}
for name,version,repository,commit,blob in (
    ('langchain-text-splitters','1.1.2','langchain-ai/langchain','58c4e5bbdd9d18d7fe7ec83b5a05477d06fe2a8e','3957738673765912ca83a0048f833e095d61e087'),
    ('langsmith','0.11.2','langchain-ai/langsmith-sdk','70933ea19d5fa0a182369930b63552aa36135af9','136a251bf3a98cd47ba4c4cf5d2517d37236eaa1')):
    relative=f'licenses/python/{name}-{version}/UPSTREAM-LICENSE.txt'
    path=collector.fetch(f'https://raw.githubusercontent.com/{repository}/{commit}/LICENSE',relative,immutable=True)
    raw=path.read_bytes();assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==blob
    records.append({'package':name,'version':version,'repository':repository,'release_commit':commit,'license_git_blob':blob,'material':relative,'reason':'Neither installed wheel nor published sdist includes a separate license file; exact official release root LICENSE preserved'})
tokenizers=folder/'python/tokenizers-0.23.1.tar.gz';expected=metadata['downloads']['python/tokenizers-0.23.1.tar.gz']['sha256'];assert checksum(tokenizers)==expected
with tarfile.open(tokenizers)as archive:
    raw=archive.extractfile('tokenizers-0.23.1/tokenizers/LICENSE').read()
    destination=folder/'licenses/python/tokenizers-0.23.1/UPSTREAM-LICENSE.txt';destination.write_bytes(raw)
records.append({'package':'tokenizers','version':'0.23.1','source_archive':'python/tokenizers-0.23.1.tar.gz','source_sha256':expected,'member':'tokenizers-0.23.1/tokenizers/LICENSE','material':'licenses/python/tokenizers-0.23.1/UPSTREAM-LICENSE.txt'})
for source in sorted((folder/'python').glob('*.tar.gz')):
    # NumPy/zstandard source distributions contain unrelated Cargo fixtures;
    # only the installed native Rust wheel sources belong to this review.
    if source.name.split('-')[0]not in {'bcrypt','chromadb','cryptography','hf_xet','jiter','orjson','ormsgpack','pydantic_core','rpds_py','tokenizers','uuid_utils','watchfiles'}:continue
    with tarfile.open(source)as archive:
        locks=[m for m in archive.getmembers()if m.isfile()and Path(m.name).name=='Cargo.lock']
        if not locks:continue
        shallowest=min(len(Path(m.name).parts)for m in locks)
        for member in locks:
            if len(Path(member.name).parts)!=shallowest:continue
            raw=archive.extractfile(member).read();relative='wheel-rust/locks/'+source.name+'/'+member.name
            path=folder/relative
            if not path.resolve().is_relative_to(folder):raise RuntimeError('Cargo lock path escapes bundle')
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            generated[relative]={'source_archive':source.relative_to(folder).as_posix(),'source_sha256':checksum(source),'member':member.name,'sha256':hashlib.sha256(raw).hexdigest()}
            for row in tomllib.loads(raw.decode())['package']:
                if not row.get('source'):continue
                if row['source'].startswith('git+'):
                    match=re.fullmatch(r'git\+https://github.com/([\w.-]+/[\w.-]+?)(?:\.git)?(?:\?[^#]*)?#([a-f0-9]{40})',row['source'])
                    if not match:raise RuntimeError('Review non-GitHub crate source: '+row['source'])
                    git_sources[match[1],match[2]]=row['source'];continue
                if row['source']!='registry+https://github.com/rust-lang/crates.io-index':raise RuntimeError('Review non-registry crate source: '+row['source'])
                key=row['name'],row['version'];assert re.fullmatch(r'[a-f0-9]{64}',row['checksum'])
                if key in crates:assert crates[key]['checksum']==row['checksum']
                crates[key]=row
def collect_crate(item):
    (name,version),row=item
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',name)or not re.fullmatch(r'[a-zA-Z0-9.+_-]+',version):raise RuntimeError('Invalid crate identity')
    path=collector.fetch(f'https://static.crates.io/crates/{name}/{name}-{version}.crate',f'wheel-rust/crates/{name}-{version}.crate',row['checksum'])
    notices=[]
    with tarfile.open(path)as archive:
        for member in archive.getmembers():
            if not member.isfile()or not re.search(r'(licen[cs]e|notice|copying|copyright)',Path(member.name).name,re.I):continue
            destination=folder/'licenses/native-rust'/f'{name}-{version}'/member.name
            if not destination.resolve().is_relative_to(folder):raise RuntimeError('Crate notice escapes bundle')
            destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(archive.extractfile(member).read());notices.append(member.name)
    if not notices:
        with tarfile.open(path)as archive:
            manifest=archive.extractfile(f'{name}-{version}/Cargo.toml').read()
            declaration=tomllib.loads(manifest.decode())['package'];license=declaration.get('license','')
            ids=[identifier.strip()for identifier in re.split(r'\s+OR\s+|/',license)]
            choices=[identifier for identifier in ids if identifier in canonical_terms]
            if not choices:raise RuntimeError('Review undeclared/unsupported crate license: '+name+' '+license)
            selected='Apache-2.0'if 'Apache-2.0'in choices else 'MIT'if 'MIT'in choices else choices[0]
            destination=folder/'licenses/native-rust'/f'{name}-{version}';destination.mkdir(parents=True,exist_ok=True)
            (destination/'upstream-Cargo.toml').write_bytes(manifest)
            (destination/'DECLARATION.txt').write_text('The unchanged published crate declares '+license+' but contains no separate notice file. This distribution selects its explicitly offered '+selected+' option. Original package authors, source comments and README are preserved alongside canonical SPDX terms. Template placeholders in canonical terms are not invented copyright claims. Full original source archive and checksum remain available.\n',encoding='utf-8')
            copyrights=[]
            for member in archive.getmembers():
                if not member.isfile():continue
                if Path(member.name).name.lower().startswith('readme')or Path(member.name).name=='.cargo_vcs_info.json':
                    (destination/Path(member.name).name).write_bytes(archive.extractfile(member).read())
                elif member.name.endswith(('.rs','.c','.h','.cpp')):
                    raw=archive.extractfile(member).read().decode('utf-8',errors='replace')
                    copyrights.extend(member.name+': '+line for line in raw.splitlines()if 'copyright'in line.lower())
            (destination/'ORIGINAL-COPYRIGHT-LINES.txt').write_text('\n'.join(copyrights)+'\n',encoding='utf-8')
            shutil.copyfile(canonical_terms[selected],destination/(selected+'.txt'))
            notices=['Original Cargo declaration, source copyright/README and canonical '+selected+' terms selected from '+license+'; no separate upstream notice was packaged']
    return {'name':name,'version':version,'checksum':row['checksum'],'notices':notices}
git_records=[]
for (repository,commit),origin in git_sources.items():
    path=collector.fetch(f'https://codeload.github.com/{repository}/tar.gz/{commit}',f'wheel-rust/git/{repository.replace("/","-")}-{commit}.tar.gz',immutable=True)
    notices=[]
    with tarfile.open(path)as archive:
        for member in archive.getmembers():
            if not member.isfile()or not re.search(r'(licen[cs]e|notice|copying|copyright)',Path(member.name).name,re.I):continue
            destination=folder/'licenses/native-rust-git'/repository/commit/member.name
            if not destination.resolve().is_relative_to(folder):raise RuntimeError('Git notice escapes bundle')
            destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(archive.extractfile(member).read());notices.append(member.name)
    if not notices:raise RuntimeError('Git source has no separate notice: '+repository)
    git_records.append({'repository':repository,'commit':commit,'origin':origin,'archive_sha256':checksum(path),'notices':notices})
print('Collecting '+str(len(crates))+' unique locked registry crates',flush=True)
canonical_terms={}
for identifier in ('MIT','Apache-2.0','BSD-2-Clause','BSD-3-Clause','BSL-1.0','Zlib','ISC','CC0-1.0','Unlicense','MPL-2.0'):
    canonical_terms[identifier]=collector.fetch('https://raw.githubusercontent.com/spdx/license-list-data/31846481de7ea17d92f2f46fad76abe58b2e9bb0/text/'+identifier+'.txt','licenses/native-rust-terms/'+identifier+'.txt',immutable=True)
(folder/'wheel-rust/crates').mkdir(parents=True,exist_ok=True)
crate_records=[];failures=[]
with ThreadPoolExecutor(max_workers=6)as pool:
    jobs={pool.submit(collect_crate,item):item[0]for item in sorted(crates.items())}
    for job in as_completed(jobs):
        try:crate_records.append(job.result())
        except Exception as error:
            failures.append({'crate':jobs[job],'error':str(error),'type':type(error).__name__})
            print('Crate review failed: '+json.dumps(failures[-1]),flush=True)
if failures:raise RuntimeError('Crate source/notice collection incomplete: '+json.dumps(failures))
crate_records.sort(key=lambda row:(row['name'],row['version']))
supplements={'missing_wheel_notices':records,'native_rust_source_locks':generated,'crate_sources_and_notices':crate_records,'git_sources':git_records,'boundary':'Conservative published source-lock dependency set, including build/dev/platform entries; no exact upstream wheel linkage or bit-for-bit rebuild claim'}
(folder/'licenses/REVIEW-SUPPLEMENTS.json').write_text(json.dumps(supplements,indent=2)+'\n',encoding='utf-8')
if Path(__file__).resolve()!=(folder/'licenses/SUPPLEMENT-EXTRACTION.py').resolve():
    shutil.copyfile(Path(__file__),folder/'licenses/SUPPLEMENT-EXTRACTION.py')
metadata['downloads']=dict(sorted(collector.downloads.items()));metadata['review_supplements']=supplements
(folder/'manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
files={folder/p for p in collector.downloads};files.update(folder/p for p in generated)
files.update(p for name in ('licenses','project','runtime-provenance')for p in (folder/name).rglob('*')if p.is_file());files.add(folder/'manifest.json')
(folder/'SHA256SUMS').write_text(''.join(f'{checksum(p)}  {p.relative_to(folder).as_posix()}\n'for p in sorted(files)),encoding='utf-8');files.add(folder/'SHA256SUMS')
with zipfile.ZipFile(args.bundle,'x',compression=zipfile.ZIP_STORED)as archive:
    for path in sorted(files):archive.write(path,path.relative_to(folder).as_posix())
with zipfile.ZipFile(args.bundle)as archive:assert archive.testzip()is None
print(json.dumps({'passed':True,'supplemented_wheels':len(records),'crate_sources_and_notices':len(crate_records),'source_downloads':len(collector.downloads),'bundle_bytes':args.bundle.stat().st_size}),flush=True)
