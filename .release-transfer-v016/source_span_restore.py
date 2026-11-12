"""Restore exact ZIP bytes using a gzip skeleton and verified old public spans.

This is byte transport, never source/application compilation. No ZIP extraction
or path-derived write is performed. Every reused span and the final file are hashed.
"""
import gzip
import hashlib
import json
from pathlib import Path

def digest(path):
    with Path(path).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def restore(skeleton,recipe,base,destination):
    assert digest(base)==recipe['base']['sha256'] and Path(base).stat().st_size==recipe['base']['bytes']
    assert digest(skeleton)==recipe['skeleton_sha256']
    previous=0
    for row in recipe['segments']:
        assert type(row['offset'])is int and type(row['base_offset'])is int and type(row['bytes'])is int
        assert previous<=row['offset'] and row['bytes']>=0 and row['offset']+row['bytes']<=recipe['bytes']
        assert 0<=row['base_offset'] and row['base_offset']+row['bytes']<=recipe['base']['bytes']
        previous=row['offset']+row['bytes']
    total=0
    with gzip.open(skeleton,'rb')as stream,Path(destination).open('xb')as output:
        while block:=stream.read(1024*1024):
            total+=len(block);assert total<=recipe['bytes'];output.write(block)
    assert total==recipe['bytes']
    with Path(base).open('rb')as source,Path(destination).open('r+b')as output:
        for row in recipe['segments']:
            source.seek(row['base_offset']);output.seek(row['offset'])
            remaining=row['bytes'];checksum=hashlib.sha256()
            while remaining:
                block=source.read(min(1024*1024,remaining));assert block
                checksum.update(block);output.write(block);remaining-=len(block)
            assert checksum.hexdigest()==row['sha256']
    assert Path(destination).stat().st_size==recipe['bytes'] and digest(destination)==recipe['sha256']
    return {'passed':True,'bytes':recipe['bytes'],'sha256':recipe['sha256'],'source_spans_verified':len(recipe['segments']),'application_rebuild':False}
