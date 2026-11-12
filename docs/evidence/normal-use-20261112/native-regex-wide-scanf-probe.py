import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sysconfig

targets = {'regcomp','regexec','re_comp','re_exec','re_compile_pattern','re_search','re_match','glob','glob64'}
seen, imports, needed, errors = set(), [], [], []
count = 0
for directory, children, names in os.walk('/usr'):
    children[:] = [name for name in children if name != '__pycache__']
    for name in names:
        path = (Path(directory)/name).resolve()
        if path in seen or not path.is_file(): continue
        seen.add(path)
        with path.open('rb') as stream:
            if stream.read(4) != b'\x7fELF': continue
        count += 1
        result = subprocess.run(['/probe/readelf','--dyn-syms','--dynamic','--wide',str(path)],
                                capture_output=True,text=True,env={**os.environ,'LD_LIBRARY_PATH':'/probe'})
        if result.returncode:
            errors.append({'file':str(path),'error':result.stderr}); continue
        found = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts)>=8 and parts[6]=='UND':
                symbol = parts[7].split('@')[0]
                if symbol.lstrip('_') in targets or 'wscanf' in symbol or symbol.startswith(('sd_journal_','journal_file_')):
                    found.append(symbol)
        row = {'file':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        if found: imports.append({**row,'imports':sorted(set(found))})
        libraries = [line.split('[')[1].split(']')[0] for line in result.stdout.splitlines()
                     if '(NEEDED)' in line and '[' in line]
        if any('systemd' in library or 'udev' in library for library in libraries):
            needed.append({**row,'needed':libraries})
root = Path(sysconfig.get_paths()['purelib'])
identifiers = ('sd_journal_','libsystemd','/run/log/journal','/var/log/journal',
               'import systemd','from systemd','import pystemd','from pystemd','cysystemd')
python_files, candidates, parse_errors = 0, [], []
for path in sorted(root.rglob('*.py')):
    python_files += 1
    text = path.read_text(encoding='utf-8-sig',errors='replace')
    if not any(key in text for key in identifiers): continue
    try: ast.parse(text)
    except SyntaxError as error: parse_errors.append({'file':str(path),'error':str(error)})
    candidates.append({'file':str(path),'sha256_lf':hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()})
print(json.dumps({'elf_files':count,'errors':errors,'sensitive_dynamic_imports':imports,
                  'systemd_needed_libraries':needed,'installed_python_source_files':python_files,
                  'journal_source_candidates':candidates,'parse_errors':parse_errors,
                  'installed_distributions':sorted(({'name':d.metadata['Name'],'version':d.version}
                     for d in importlib.metadata.distributions()),key=lambda x:x['name'].lower()),
                  'boundary':'Actual regex, wide-scanf and journal dynamic imports plus installed source candidates. No claim about arbitrary administrator calls, static copies, dlsym or future plugins.'},indent=2))
