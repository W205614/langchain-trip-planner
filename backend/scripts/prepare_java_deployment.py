"""Split existing local configuration without sending DB credentials to the agent.

Creates private files only in a new directory; never overwrites an existing setup.
Creates a new normal-mode deployment; existing private files are never overwritten.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import base64
from urllib.parse import urlsplit
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
BUSINESS_SETTINGS = ("PUBLIC_BASE_URL", "PUBLIC_REGISTRATION_MODE", "ADMIN_MFA_REQUIRED",
    "DB_POOL_MAX_SIZE", "DB_POOL_MIN_IDLE",
    "CORS_ORIGINS", "UPLOAD_STORAGE_BACKEND", "UPLOAD_DIR", "S3_ENDPOINT", "S3_REGION", "S3_BUCKET", "S3_PREFIX",
    "S3_PATH_STYLE", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_SESSION_TOKEN", "S3_ALLOW_HTTP",
    "SMTP_ENABLED", "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM",
    "SMTP_SECURITY", "OUTBOUND_REQUIRE_ACTOR_CONTEXT",
    "OUTBOUND_USER_DAILY_TEXT_LIMIT", "OUTBOUND_USER_DAILY_EMBEDDING_LIMIT", "OUTBOUND_USER_DAILY_AMAP_LIMIT",
    "TRIP_INPUT_PRICE_PER_MILLION_USD", "TRIP_OUTPUT_PRICE_PER_MILLION_USD")


def model_settings(source):
    values = {k:v for k,v in source.items() if k.startswith(("LLM_", "OPENAI_", "EMBEDDING_", "VISION_", "RAG_"))}
    for key,value in {"LLM_MODEL_ID":"gpt-6.1-sol", "LLM_BASE_URL":"https://api.openai.com/v1",
        "LLM_REASONING_EFFORT":"low", "VISION_MODEL_ID":"gpt-6.1-sol", "VISION_REASONING_EFFORT":"low",
        "EMBEDDING_MODEL":"text-embedding-3-large", "EMBEDDING_DIMENSIONS":"3072"}.items():
        if not values.get(key): values[key]=value
    return values


def _validate(values):
    if any(value is None or "\n" in value or "\r" in value or "'" in value for value in values.values()):
        raise ValueError("Config needs unsupported multiline/quote escaping; no partial deployment")


def _write(path, values):
    _validate(values)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        if os.name != "nt":
            os.fchmod(stream.fileno(), 0o600)
        stream.write("".join(f"{key}='{value}'\n" for key,value in values.items()))
    temporary.replace(path)


def prepare(output, public=False):
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
    dataset = secrets.token_hex(16)
    business.update(DATASET_ID=dataset, DATASET_ENROLL_NEW="true", ACCESS_TOKEN_EXPIRE_MINUTES="120",
                    ACCOUNT_SECURITY_ENCRYPTION_KEY=base64.b64encode(secrets.token_bytes(32)).decode("ascii"))
    for key in ("ACCESS_TOKEN_EXPIRE_MINUTES", "BOOTSTRAP_ADMIN_USERNAME", "LLM_REQUEST_MAX_CONCURRENCY",
        "TRIP_TASK_TIMEOUT_SECONDS", "TRIP_TASK_QUEUE_LIMIT", "TRIP_USER_ACTIVE_LIMIT", "TRIP_USER_DAILY_LIMIT", "TRIP_GLOBAL_DAILY_LIMIT",
        "AMAP_REST_BASE_URL", "AMAP_POI_CACHE_TTL_SECONDS", "AMAP_WEATHER_CACHE_TTL_SECONDS",
        "AMAP_DETAIL_CACHE_TTL_SECONDS", "AMAP_CACHE_MAXIMUM_SIZE"):
        if source.get(key):
            business[key] = source[key]
    business.update({key:source[key] for key in BUSINESS_SETTINGS if key in source})
    agent = model_settings(source)
    agent.update(AMAP_API_KEY=source.get("AMAP_MCP_API_KEY") or source.get("AMAP_API_KEY", ""),
        AMAP_TRANSPORT="mcp", AMAP_MCP_URL=source.get("AMAP_MCP_URL", "https://mcp.amap.com/mcp"))
    agent.update(INTERNAL_SERVICE_KEY=internal, BUSINESS_URL="http://backend:9000", DATA_DIR="/app/runtime",
        CHROMA_DIR="/app/data/chroma", APP_ENV="development", ACCEPTANCE_BUDGET_FILE="")
    agent.update(DATASET_ID=dataset, DATASET_ENROLL_NEW="true")
    public_values = None
    if public:
        origin=urlsplit(source.get("PUBLIC_BASE_URL", ""))
        if origin.scheme!="https" or not origin.hostname or origin.path not in ("", "/") or origin.query or origin.fragment or origin.username is not None or origin.password is not None:
            raise ValueError("Public deployment requires PUBLIC_BASE_URL=https://your-domain")
        if source.get("SMTP_ENABLED", "").lower()!="true" or not source.get("SMTP_HOST") or not source.get("SMTP_FROM") or source.get("SMTP_SECURITY","starttls")=="none":
            raise ValueError("Public deployment requires a configured TLS SMTP service")
        if not source.get("ACME_EMAIL"):
            raise ValueError("Public deployment requires ACME_EMAIL for certificate issuance")
        business.update(APP_ENV="production",PUBLIC_REGISTRATION_MODE="email_verified",ADMIN_MFA_REQUIRED="true",OUTBOUND_REQUIRE_ACTOR_CONTEXT="true")
        agent.update(APP_ENV="production",OUTBOUND_LEDGER_REQUIRED="true")
        public_values={"PUBLIC_HOSTNAME":origin.hostname,"ACME_EMAIL":source["ACME_EMAIL"],
            "TRIP_PRODUCTION_RUNTIME":str(output.resolve()),"PUBLIC_EDGE_CIDR":source.get("PUBLIC_EDGE_CIDR","172.30.244.0/28")}
        if source.get("TRIP_POSTGRES_CA_DIR"):
            public_values["TRIP_POSTGRES_CA_DIR"]=source["TRIP_POSTGRES_CA_DIR"]
    configs = (("postgres", postgres), ("business", business), ("agent", agent))
    for _, values in configs:
        # Compose env_file supports single quoted values, without dollar expansion.
        _validate(values)
    if public_values is not None: _validate(public_values)
    output = output.resolve()
    if os.name == "nt":
        output.mkdir(parents=True, exist_ok=False)
    else:
        output.mkdir(parents=True, mode=0o700, exist_ok=False)
    for name, values in configs:
        _write(output / f"{name}.env", values)
    if public_values is not None:
        _write(output / "public.env",public_values)
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
    business.setdefault("ACCOUNT_SECURITY_ENCRYPTION_KEY",base64.b64encode(secrets.token_bytes(32)).decode("ascii"))
    business["ACCESS_TOKEN_EXPIRE_MINUTES"]=str(max(5,min(int(business.get("ACCESS_TOKEN_EXPIRE_MINUTES") or "120"),120)))
    business.update({key:source[key] for key in BUSINESS_SETTINGS if key in source})
    # Only explicitly supplied model settings replace existing private values.
    agent.update({k:v for k,v in source.items() if k.startswith(("LLM_", "OPENAI_", "EMBEDDING_", "VISION_", "RAG_"))})
    for key in ("AMAP_REST_BASE_URL", "AMAP_POI_CACHE_TTL_SECONDS", "AMAP_WEATHER_CACHE_TTL_SECONDS",
                "AMAP_DETAIL_CACHE_TTL_SECONDS", "AMAP_CACHE_MAXIMUM_SIZE"):
        if source.get(key):
            business[key] = source[key]
    agent.update(AMAP_API_KEY=mcp_key, AMAP_TRANSPORT="mcp",
                 AMAP_MCP_URL=source.get("AMAP_MCP_URL", "https://mcp.amap.com/mcp"))
    forbidden = {"JWT_SECRET_KEY", "POSTGRES_PASSWORD", "DATABASE_URL", "JDBC_DATABASE_URL"}
    if forbidden.intersection(agent):
        raise RuntimeError("Agent runtime config contains business database credentials")
    if os.name != "nt":
        output.chmod(0o700)
        for path in required:
            path.chmod(0o600)
    _write(output / "business.env", business)
    _write(output / "agent.env", agent)
    print(json.dumps({"private_config": str(output), "upgraded": True,
                      "credentials_rotated": False, "agent_has_business_credentials": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "deploy/runtime")
    parser.add_argument("--upgrade-existing", action="store_true")
    parser.add_argument("--public", action="store_true", help="Prepare a new public deployment with verified email and TLS")
    args = parser.parse_args()
    if args.upgrade_existing and args.public:
        parser.error("--public creates a new installation; use the documented existing-data migration")
    if args.upgrade_existing: upgrade(args.output)
    else: prepare(args.output,public=args.public)
