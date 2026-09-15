"""Split existing local configuration without sending DB credentials to the agent.

Creates private files only in a new directory; never overwrites an existing setup.
Creates a new normal-mode deployment; existing private files are never overwritten.
"""
import argparse
import json
from pathlib import Path
import secrets
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def prepare(output):
    source = dotenv_values(ROOT / "backend/.env")
    source = {key: value for key, value in source.items() if value is not None}
    secret = source.get("JWT_SECRET_KEY") or secrets.token_urlsafe(48)
    if len(secret.encode()) < 32:
        raise RuntimeError("JWT_SECRET_KEY must contain at least 32 bytes; leave it empty for a new random key")
    internal = secrets.token_urlsafe(48)
    postgres = {"POSTGRES_USER": "trip", "POSTGRES_PASSWORD": secrets.token_urlsafe(32), "POSTGRES_DB": "trip_java"}
    business = {**postgres, "JWT_SECRET_KEY": secret, "INTERNAL_SERVICE_KEY": internal,
        "JDBC_DATABASE_URL": "jdbc:postgresql://postgres:5432/trip_java", "AGENT_URL": "http://agent:9000",
        "UPLOAD_DIR": "/data/uploads", "WORKERS_ENABLED": "true", "APP_ENV": "development"}
    for key in ("ACCESS_TOKEN_EXPIRE_MINUTES", "BOOTSTRAP_ADMIN_USERNAME", "LLM_REQUEST_MAX_CONCURRENCY",
        "TRIP_TASK_TIMEOUT_SECONDS", "TRIP_TASK_QUEUE_LIMIT", "TRIP_USER_ACTIVE_LIMIT", "TRIP_USER_DAILY_LIMIT", "TRIP_GLOBAL_DAILY_LIMIT"):
        if source.get(key):
            business[key] = source[key]
    agent = {k:v for k,v in source.items() if k.startswith(("AMAP_", "LLM_", "OPENAI_", "EMBEDDING_", "VISION_", "RAG_"))}
    agent.update(INTERNAL_SERVICE_KEY=internal, BUSINESS_URL="http://backend:9000", DATA_DIR="/app/data",
        CHROMA_DIR="/app/data/chroma", APP_ENV="development", ACCEPTANCE_BUDGET_FILE="")
    configs = (("postgres", postgres), ("business", business), ("agent", agent))
    for _, values in configs:
        # Compose env_file supports single quoted values, without dollar expansion.
        if any("\n" in value or "\r" in value or "'" in value for value in values.values()):
            raise ValueError("Config needs unsupported multiline/quote escaping; no partial deployment")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name, values in configs:
        (output / f"{name}.env").write_text("".join(f"{key}='{value}'\n" for key,value in values.items()),encoding="utf-8")
    print(json.dumps({"private_config": str(output), "workers_enabled": True, "agent_has_business_credentials": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "deploy/runtime")
    prepare(parser.parse_args().output)
