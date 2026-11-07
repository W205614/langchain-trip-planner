"""Read the backend's exact single-host JDBC target using a PostgreSQL17 client.

This is a maintenance client, never a PostgreSQL server. Only explicitly mapped
JDBC options are accepted; restore clients must not inherit this environment.
"""
from __future__ import annotations

import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import uuid4

from deployment_assets import docker, inspect, sql

CLIENT_IMAGE = "trip-planner-postgres:17.11-security1"
OPTIONS = {"sslmode", "sslrootcert", "sslcert", "sslkey", "connectTimeout", "ApplicationName"}


def private_directory(path: Path, *, exist_ok=False):
    if os.name == "nt":
        # Inherit the operator-selected private parent's NTFS ACL. Python's
        # Windows mode0700 ACL uses OWNER RIGHTS, which can exclude the operator
        # from files subsequently created by Docker's service identity.
        path.mkdir(parents=True, exist_ok=exist_ok)
    else:
        path.mkdir(parents=True, mode=0o700, exist_ok=exist_ok)
        path.chmod(0o700)


def target(backend: dict, postgres: dict | None = None) -> dict:
    values = dict(item.split("=", 1) for item in backend["Config"]["Env"] if "=" in item)
    normalized = {re.sub(r"[.-]", "_", key).upper() for key in values}
    if any(key.startswith(("SPRING_DATASOURCE_", "SPRING_CONFIG_", "SPRING_PROFILES_")) or key == "SPRING_APPLICATION_JSON" for key in normalized):
        raise ValueError("Backup supports only project-standard JDBC_DATABASE_URL/POSTGRES_USER/POSTGRES_PASSWORD; Spring target/config overrides are ambiguous")
    launch = " ".join([*(backend["Config"].get("Entrypoint") or []), *(backend["Config"].get("Cmd") or []),
                       *(values.get(key, "") for key in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "JAVA_OPTS"))])
    if re.search(r"(?:-D|--)(?:spring[._-](?:datasource|config|profiles|application[._-]json)|JDBC_DATABASE_URL|POSTGRES_(?:USER|PASSWORD))(?:[.=_-]|\s|$)", launch, re.IGNORECASE):
        raise ValueError("Datasource or Spring configuration launch overrides are unsupported for exact-target backup")
    raw = values.get("JDBC_DATABASE_URL", "jdbc:postgresql://localhost:5432/trip")
    if not raw.startswith("jdbc:postgresql://") or any(c in raw for c in "\r\n\x00") or re.search(r"%(?![a-fA-F0-9]{2})", raw):
        raise ValueError("Backup requires an explicit single-host jdbc:postgresql://host:port/database URL")
    parsed = urlsplit(raw[5:])
    if parsed.username is not None or parsed.password is not None or parsed.fragment or not parsed.hostname or "," in parsed.netloc:
        raise ValueError("JDBC credentials, fragments and multiple hosts are not supported")
    host = parsed.hostname
    host_config = backend.get("HostConfig", {})
    if any(host_config.get(key) for key in ("Dns", "DnsSearch", "DnsOptions", "Links")) or any(entry.split(":", 1)[0] == host for entry in host_config.get("ExtraHosts") or []):
        raise ValueError("Custom backend DNS/host mapping can change the JDBC target; this backup adapter supports standard Docker network DNS only")
    if "%" in host:
        raise ValueError("Scoped/encoded JDBC hostnames are unsupported")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
        if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", host):
            raise ValueError("Unsupported JDBC hostname") from None
    port = 5432 if parsed.port is None else parsed.port
    if not 1 <= port <= 65535:
        raise ValueError("Invalid JDBC port")
    database = unquote(parsed.path.removeprefix("/"), encoding="utf-8", errors="strict")
    user = values.get("POSTGRES_USER", "trip")
    if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_$.-]{0,62}", database) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_$.-]{0,62}", user):
        raise ValueError("Backup supports explicit simple PostgreSQL database and user names up to63 characters")
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, encoding="utf-8", errors="strict")
    options = dict(pairs)
    if len(options) != len(pairs) or options.keys() - OPTIONS or any(not value or any(c in value for c in "\r\n\x00") for value in options.values()):
        raise ValueError("Duplicate, empty or unsupported JDBC option; backup will not silently ignore it")
    aliases = set()
    if postgres:
        aliases.add(postgres.get("Name", "").lstrip("/"))
        for network in postgres.get("NetworkSettings", {}).get("Networks", {}).values():
            aliases.update(network.get("Aliases") or [])
            aliases.add(network.get("IPAddress", ""))
    local = host in aliases and port == 5432
    if host == "localhost" or address is not None and address.is_loopback:
        raise ValueError("Loopback JDBC targets cannot be reproduced from a separate backup client; use the actual database hostname")
    mode = options.get("sslmode", "prefer" if local else "")
    if mode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"} or not local and mode != "verify-full":
        raise ValueError("External PostgreSQL backup requires explicit sslmode=verify-full")
    if mode in {"verify-ca", "verify-full"} and "sslrootcert" not in options:
        raise ValueError("Verified TLS backup requires explicit sslrootcert from a read-only backend mount")
    if ("sslcert" in options) != ("sslkey" in options):
        raise ValueError("Client TLS certificate and key must be configured together")
    password = values.get("POSTGRES_PASSWORD", "")
    if any(c in password for c in "\r\n\x00"):
        raise ValueError("Multiline PostgreSQL credentials cannot be projected into a Docker env file")
    timeout = options.get("connectTimeout", "10")
    if not timeout.isdecimal() or not 1 <= int(timeout) <= 300:
        raise ValueError("Backup connectTimeout must be1..300 seconds")
    return {"host": host, "port": port, "database": database, "user": user,
            "password": password, "sslmode": mode, "options": options, "local": local,
            "connect_timeout": timeout}


def mounted_certificate(backend: dict, filename: str) -> bytes:
    path = PurePosixPath(filename)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("TLS certificate paths must be absolute backend paths")
    mounts = [mount for mount in backend["Mounts"] if path.is_relative_to(PurePosixPath(mount["Destination"]))]
    if not mounts or max(mounts, key=lambda m: len(m["Destination"])).get("RW", True):
        raise ValueError("TLS file must belong to an actual read-only backend mount")
    raw = docker("cp", f"{backend['Id']}:{path}", "-")
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        members = archive.getmembers()
        if len(members) != 1 or not members[0].isfile() or not 0 < members[0].size <= 1024 * 1024:
            raise ValueError("TLS file must be a regular file of at most1MiB; links are not followed")
        return archive.extractfile(members[0]).read()


class DatabaseClient:
    def __init__(self, deployed: dict, directory: Path):
        self.backend = deployed["backend"]
        self.target = target(self.backend, deployed.get("postgres"))
        networks = self.backend["NetworkSettings"]["Networks"]
        if len(networks) != 1:
            raise ValueError("Backup currently requires one unambiguous backend Docker network")
        self.network = next(iter(networks))
        self.directory = directory
        self.name = "trip-backup-pg-client-" + uuid4().hex[:12]
        self.container = None

    def __enter__(self):
        private_directory(self.directory)
        tls = self.directory / "tls"
        private_directory(tls)
        value = self.target
        environment = {"PGHOST": value["host"], "PGPORT": str(value["port"]), "PGDATABASE": value["database"],
                       "PGUSER": value["user"], "PGPASSWORD": value["password"], "PGSSLMODE": value["sslmode"],
                       "PGCONNECT_TIMEOUT": value["connect_timeout"], "PGAPPNAME": value["options"].get("ApplicationName", "trip-maintenance-backup"),
                       "PGOPTIONS": "-c default_transaction_read_only=on", "PGGSSENCMODE": "disable"}
        key = None
        for option, variable in (("sslrootcert", "PGSSLROOTCERT"), ("sslcert", "PGSSLCERT"), ("sslkey", "PGSSLKEY")):
            if option not in value["options"]:
                continue
            content = mounted_certificate(self.backend, value["options"][option])
            if option == "sslkey":
                from cryptography.hazmat.primitives import serialization
                try:
                    loader = serialization.load_pem_private_key if content.startswith(b"-----BEGIN") else serialization.load_der_private_key
                    private = loader(content, password=None)
                except (ValueError, TypeError):
                    raise ValueError("Backup supports only unencrypted PEM or PKCS8 DER client keys; PKCS12/password callbacks are unsupported") from None
                original = tls / "backend-sslkey.original"
                original.write_bytes(content);original.chmod(0o600)
                content = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
                key = tls / "client.key"
                key.write_bytes(content);key.chmod(0o600)
                environment[variable] = "/tmp/trip-pg-client.key"
            else:
                name = option + ".pem"
                (tls / name).write_bytes(content)
                environment[variable] = "/run/pg-backup-tls/" + name
        private_env = self.directory / "postgres-client.env"
        private_env.write_text("".join(f"{k}={v}\n" for k, v in environment.items()), encoding="utf-8")
        private_env.chmod(0o600)
        try:
            self.container = docker("run", "-d", "--name", self.name, "--network", self.network,
                                    "--label", "codex.task=trip-pg-backup-client", "--env-file", private_env,
                                    "--mount", f"type=bind,source={tls.resolve()},target=/run/pg-backup-tls,readonly",
                                    "--tmpfs", "/var/lib/postgresql/data:rw,size=16m,mode=0700",
                                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                                    "--entrypoint", "sleep", CLIENT_IMAGE, "infinity").decode().strip()
            if key:
                docker("cp", key, f"{self.container}:/tmp/trip-pg-client.key")
                docker("exec", self.container, "chmod", "600", "/tmp/trip-pg-client.key")
            actual = json.loads(sql(self.container, value["database"], "SELECT json_build_object('database',current_database(),'user',current_user,'version',current_setting('server_version_num')::int,'ssl',COALESCE((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),false))", value["user"]))
            if not 170000 <= actual["version"] < 180000 or actual["database"] != value["database"] or actual["user"] != value["user"]:
                raise ValueError("Backup requires PostgreSQL17 and the exact backend database/user")
            if value["sslmode"] in {"require", "verify-ca", "verify-full"} and not actual["ssl"]:
                raise ValueError("Database client did not negotiate the requested TLS")
            self.image = inspect(self.container)["Image"]
            self.provenance = {"source": "backend-effective-jdbc", "mode": "local_compose" if value["local"] else "external_postgresql",
                               "host": value["host"], "port": value["port"], "database": value["database"], "user": value["user"],
                               "sslmode": value["sslmode"], "server_version_num": actual["version"], "server_ssl": actual["ssl"],
                               "client_container": self.name, "client_image": self.image, "client_image_reference": CLIENT_IMAGE,
                               "network": self.network, "credential_projection": "PG connection settings only; private env; no JWT/model keys",
                               "tls_backend_paths": {k: v for k, v in value["options"].items() if k.startswith("ssl") and k != "sslmode"}}
            return self
        except BaseException:
            if self.container:
                docker("stop", self.container)
            raise

    def __exit__(self, *error):
        docker("stop", self.container)
