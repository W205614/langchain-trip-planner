import os
from pathlib import Path
import subprocess
import sys


def test_internal_runtime_does_not_import_business_orm():
    code="""
import importlib,sys
for name in ('app.agent_api.main','app.agent_api.extraction','app.agent_api.indexing',
             'app.agent_api.rebuild','app.agents.trip_planner_agent','app.api.routes.poi'):
    importlib.import_module(name)
assert not any(name=='app.db' or name.startswith('app.db.') for name in sys.modules)
"""
    env={k:v for k,v in os.environ.items() if k!="DATABASE_URL"}
    env["BUSINESS_URL"]="http://fixture-business:9000"
    subprocess.run([sys.executable,"-c",code],cwd=Path(__file__).resolve().parents[1],env=env,check=True,capture_output=True)
