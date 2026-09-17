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


def _validate(values):
    if any(value is None or "\n" in value or "\r" in value or "'" in value for value in values.values()):
        raise ValueError("Config needs unsupported multiline/quote escaping; no partial deployment")


def _write(path, values):
    _validate(values)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(f"{key}='{value}'\n" for key,value in values.items()), encoding="utf-8")
    temporary.replace(path)


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
        "UPLOAD_DIR": "/data/uploads", "WORKERS_ENABLED": "true", "APP_ENV": "development",
        "AMAP_REST_API_KEY": source.get("AMAP_REST_API_KEY") or source.get("AMAP_API_KEY", "")}
    for key in ("ACCESS_TOKEN_EXPIRE_MINUTES", "BOOTSTRAP_ADMIN_USERNAME", "LLM_REQUEST_MAX_CONCURRENCY",
        "TRIP_TASK_TIMEOUT_SECONDS", "TRIP_TASK_QUEUE_LIMIT", "TRIP_USER_ACTIVE_LIMIT", "TRIP_USER_DAILY_LIMIT", "TRIP_GLOBAL_DAILY_LIMIT",
        "AMAP_REST_BASE_URL", "AMAP_POI_CACHE_TTL_SECONDS", "AMAP_WEATHER_CACHE_TTL_SECONDS",
        "AMAP_DETAIL_CACHE_TTL_SECONDS", "AMAP_CACHE_MAXIMUM_SIZE"):
        if source.get(key):
            business[key] = source[key]
    agent = {k:v for k,v in source.items() if k.startswith(("LLM_", "OPENAI_", "EMBEDDING_", "VISION_", "RAG_"))}
    agent.update(AMAP_API_KEY=source.get("AMAP_MCP_API_KEY") or source.get("AMAP_API_KEY", ""),
        AMAP_TRANSPORT="mcp", AMAP_MCP_URL=source.get("AMAP_MCP_URL", "https://mcp.amap.com/mcp"))
    agent.update(INTERNAL_SERVICE_KEY=internal, BUSINESS_URL="http://backend:9000", DATA_DIR="/app/data",
        CHROMA_DIR="/app/data/chroma", APP_ENV="development", ACCEPTANCE_BUDGET_FILE="")
    configs = (("postgres", postgres), ("business", business), ("agent", agent))
    for _, values in configs:
        # Compose env_file supports single quoted values, without dollar expansion.
        _validate(values)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name, values in configs:
        _write(output / f"{name}.env", values)
    print(json.dumps({"private_config": str(output), "workers_enabled": True, "agent_has_business_credentials": False}))


def upgrade(output):
    """Add current service-boundary settings without rotating existing private credentials."""
    output = output.resolve()
    required = [output / name for name in ("postgres.env", "business.env", "agent.env")]
    if not output.is_dir() or not all(path.is_file() for path in required):
        raise FileNotFoundError("Existing runtime config is incomplete; create a new deployment instead")
    source = {key: value for key, value in dotenv_values(ROOT / "backend/.env").items() if value is not None}
    business = {key: value for key, value in dotenv_values(output / "business.env").items() if value is not None}
    agent = {key: value for key, value in dotenv_values(output / "agent.env").items() if value is not None}
    if len(business.get("JWT_SECRET_KEY", "").encode()) < 32 or not business.get("INTERNAL_SERVICE_KEY"):
        raise RuntimeError("Existing business config is missing required private credentials")
    if agent.get("INTERNAL_SERVICE_KEY") != business["INTERNAL_SERVICE_KEY"]:
        raise RuntimeError("Existing Java and Agent internal credentials do not match")

    rest_key = source.get("AMAP_REST_API_KEY") or source.get("AMAP_API_KEY") or business.get("AMAP_REST_API_KEY", "")
    mcp_key = source.get("AMAP_MCP_API_KEY") or source.get("AMAP_API_KEY") or agent.get("AMAP_API_KEY", "")
    business["AMAP_REST_API_KEY"] = rest_key
    for key in ("AMAP_REST_BASE_URL", "AMAP_POI_CACHE_TTL_SECONDS", "AMAP_WEATHER_CACHE_TTL_SECONDS",
                "AMAP_DETAIL_CACHE_TTL_SECONDS", "AMAP_CACHE_MAXIMUM_SIZE"):
        if source.get(key):
            business[key] = source[key]
    agent.update(AMAP_API_KEY=mcp_key, AMAP_TRANSPORT="mcp",
                 AMAP_MCP_URL=source.get("AMAP_MCP_URL", "https://mcp.amap.com/mcp"))
    forbidden = {"JWT_SECRET_KEY", "POSTGRES_PASSWORD", "DATABASE_URL", "JDBC_DATABASE_URL"}
    if forbidden.intersection(agent):
        raise RuntimeError("Agent runtime config contains business database credentials")
    _write(output / "business.env", business)
    _write(output / "agent.env", agent)
    print(json.dumps({"private_config": str(output), "upgraded": True,
                      "credentials_rotated": False, "agent_has_business_credentials": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "deploy/runtime")
    parser.add_argument("--upgrade-existing", action="store_true")
    args = parser.parse_args()
    (upgrade if args.upgrade_existing else prepare)(args.output)
