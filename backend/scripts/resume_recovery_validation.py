"""Run existing recovery drills against the dedicated resume evaluation stack."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'docs/evidence/resume-validation-20260911'
path = ROOT / 'backend/scripts/lifecycle_smoke.py'
namespace = {'__name__': 'resume_validation', '__file__': str(path)}
exec(compile(path.read_text(encoding='utf-8').replace('"trip-validation"', '"trip-resume-validation"'), str(path), 'exec'), namespace)
namespace['run'](OUTPUT / 'lifecycle.json')
namespace = runpy.run_path(str(ROOT / 'backend/scripts/recovery_drill.py'))
namespace['COMPOSE'][3] = 'trip-resume-validation'
namespace['run'](OUTPUT / 'recovery.json')
