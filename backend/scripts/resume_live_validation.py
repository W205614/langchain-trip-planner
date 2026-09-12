"""Bounded live evaluation with isolated state; no daily user records are read."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

BACKEND = Path(__file__).resolve().parents[1]
OUTPUT = BACKEND.parent / 'docs/evidence/resume-validation-20260911'
OUTPUT.mkdir(parents=True, exist_ok=True)
runtime = Path(tempfile.mkdtemp(prefix='trip-resume-live-'))
env = os.environ.copy()
env.update(DATA_DIR=str(runtime), CHROMA_DIR=str(runtime / 'chroma'),
           UPLOAD_DIR=str(runtime / 'uploads'), LOG_DIR=str(runtime / 'logs'),
           DATABASE_URL='sqlite:///' + (runtime / 'live.db').as_posix(),
           RAG_ENABLED='true', PYTHONIOENCODING='utf-8')
(runtime / 'uploads').mkdir()

def execute(name, args, timeout):
    # Keep raw provider messages outside publishable reports.
    with (runtime / (name + '.log')).open('w', encoding='utf-8') as log:
        try:
            result = subprocess.run([sys.executable, *args], cwd=BACKEND, env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            status = {'name': name, 'exit_code': result.returncode}
        except subprocess.TimeoutExpired:
            status = {'name': name, 'timeout_seconds': timeout}
    print(json.dumps(status), flush=True)
    return status

statuses = []
statuses.append(execute('initialize', ['-c', 'from app.db.database import init_db; init_db()'], 60))
if statuses[-1].get('exit_code') != 0:
    raise SystemExit('Isolated database initialization failed')
statuses.append(execute('rag', ['-m', 'app.evals.rag_benchmark', '--mode', 'live',
    '--output', str(OUTPUT / 'rag-live.json')], 360))
rag_path = OUTPUT / 'rag-live.json'
if rag_path.exists() and statuses[-1].get('exit_code') == 0:
    sys.path.insert(0, str(BACKEND))
    from app.evals.rag_benchmark import load_dataset, DEFAULT_CASES, evaluate, RetrievalResult
    report = json.loads(rag_path.read_text(encoding='utf-8'))
    if not any(row['5']['results'] for row in report['details']):
        raise SystemExit('All rankings empty: invalid index/environment, not a valid quality benchmark')
    cases, _ = load_dataset(DEFAULT_CASES)
    rankings = {row['5']['id']: [RetrievalResult(**item) for item in row['5']['results']]
                for row in report['details']}
    extra = evaluate(cases, rankings, k_values=(1, 3, 5))
    extra.update(mode='live_rankings_rescored', source='rag-live.json',
                 dataset_snapshot=report['dataset_snapshot'], run=report['run'],
                 label_counts={str(n): sum(len(c['relevant_chunk_ids']) == n for c in cases)
                               for n in sorted({len(c['relevant_chunk_ids']) for c in cases})},
                 boundary='Top-1 precision is labeled first-result hit rate, not final answer accuracy. Existing development set, not unseen test set.')
    (OUTPUT / 'rag-top1.json').write_text(json.dumps(extra, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(extra['metrics']), flush=True)

# Predeclared six scenarios; all attempts including failures remain in the output.
for city, days, slug in [('北京', 1, 'beijing-1'), ('北京', 3, 'beijing-3'),
                         ('上海', 2, 'shanghai-2'), ('广州', 2, 'guangzhou-2'),
                         ('深圳', 1, 'shenzhen-1'), ('杭州', 2, 'hangzhou-2')]:
    statuses.append(execute(slug, ['-m', 'app.evals.planning_benchmark', '--city', city,
        '--days', str(days), '--runs', '1', '--output', str(OUTPUT / (slug + '.json'))], 240))

(OUTPUT / 'live-run-status.json').write_text(json.dumps({
    'generated_at': datetime.now(timezone.utc).isoformat(), 'statuses': statuses,
    'scope': 'Current configured endpoint; isolated index and database; six predeclared Agent scenarios, not HTTP or real-world itinerary accuracy.'
}, ensure_ascii=False, indent=2), encoding='utf-8')
