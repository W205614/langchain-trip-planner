import io
import json
import os
from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backup_database as client
import deployment_assets as assets


def backend(url="jdbc:postgresql://db.example.test:5433/actual?sslmode=verify-full&sslrootcert=/run/certs/ca.pem"):
    return {"Id":"backend", "Config":{"Env":["JDBC_DATABASE_URL="+url,"POSTGRES_USER=actual_user","POSTGRES_PASSWORD=private-fixture", "JWT_SECRET_KEY=never-project", "LLM_API_KEY=never-project"]},
            "Mounts":[{"Destination":"/run/certs","RW":False}], "NetworkSettings":{"Networks":{"owned-backend":{}}}}


def test_actual_jdbc_overrides_an_unrelated_local_database():
    decoy={"Name":"/decoy", "Config":{"Env":["POSTGRES_DB=wrong","POSTGRES_USER=wrong"]}, "NetworkSettings":{"Networks":{"owned-backend":{"Aliases":["postgres"]}}}}
    selected=client.target(backend(), decoy)
    assert (selected["host"],selected["port"],selected["database"],selected["user"],selected["local"])==("db.example.test",5433,"actual","actual_user",False)
    local=client.target(backend("jdbc:postgresql://postgres/localdb"),decoy)
    assert local["local"] and local["database"]=="localdb" and local["user"]=="actual_user"


@pytest.mark.parametrize("url", [
    "jdbc:postgresql:actual", "jdbc:postgresql://db1,db2/actual?sslmode=verify-full",
    "jdbc:postgresql://user:secret@db/actual", "jdbc:postgresql://db/actual#fragment",
    "jdbc:postgresql://db/actual?sslmode=require", "jdbc:postgresql://db/actual?sslmode=verify-ca",
    "jdbc:postgresql://db/actual?sslmode=verify-full&sslrootcert=/cert&user=wrong",
    "jdbc:postgresql://db/actual?sslmode=verify-full&sslrootcert=/cert&sslmode=disable",
    "jdbc:postgresql://db/actual?sslmode=verify-full&sslrootcert=/cert&options=-csearch_path=other",
    "jdbc:postgresql://db/actual?sslmode=verify-full&sslrootcert=/cert&sslcert=/client",
    "jdbc:postgresql://db/actual?sslmode=verify-full&sslrootcert=/cert&connectTimeout=0",
    "jdbc:postgresql://db:0/actual", "jdbc:postgresql://db/actual%2Fother",
    "jdbc:postgresql://db/actual?sslmode=verify-full&sslrootcert=%ZZ",
    "jdbc:postgresql://127.0.0.1/actual?sslmode=verify-full&sslrootcert=/cert",
])
def test_ambiguous_or_unmapped_jdbc_fails_before_any_docker_operation(url):
    with pytest.raises(ValueError):client.target(backend(url))


def test_tls_file_requires_actual_read_only_mount_and_refuses_symlinks(monkeypatch):
    source=backend()
    source["Mounts"][0]["RW"]=True
    with pytest.raises(ValueError,match="read-only"):client.mounted_certificate(source,"/run/certs/ca.pem")
    source["Mounts"][0]["RW"]=False
    content=io.BytesIO()
    with tarfile.open(fileobj=content,mode="w") as archive:
        row=tarfile.TarInfo("ca.pem");row.type=tarfile.SYMTYPE;row.linkname="/elsewhere";archive.addfile(row)
    monkeypatch.setattr(client,"docker",lambda *args:content.getvalue())
    with pytest.raises(ValueError,match="regular"):client.mounted_certificate(source,"/run/certs/ca.pem")


def test_client_projects_only_database_secrets_and_stops_on_failure(tmp_path,monkeypatch):
    calls=[]
    def docker(*args):calls.append(tuple(map(str,args)));return b"client-container"
    monkeypatch.setattr(client,"docker",docker)
    monkeypatch.setattr(client,"mounted_certificate",lambda *_:b"public certificate")
    monkeypatch.setattr(client,"sql",lambda *_:json.dumps({"database":"actual","user":"actual_user","version":180001,"ssl":True}))
    with pytest.raises(ValueError,match="PostgreSQL17"):
        with client.DatabaseClient({"backend":backend()},tmp_path/"private"):pass
    env=(tmp_path/"private/postgres-client.env").read_text()
    assert "PGHOST=db.example.test\n" in env and "PGDATABASE=actual\n" in env and "PGUSER=actual_user\n" in env
    assert "PGPASSWORD=private-fixture\n" in env and "JWT" not in env and "LLM" not in env
    assert calls[-1]==("stop","client-container")
    run=calls[0];assert "--network" in run and "owned-backend" in run and "--publish" not in run and "--rm" not in run
    if os.name!="nt":assert (tmp_path/"private").stat().st_mode&0o777==0o700


def test_optional_postgres_does_not_change_default_service_contract(monkeypatch):
    records={name:{"Id":name,"Config":{"Labels":{"com.docker.compose.service":name,"com.docker.compose.oneoff":"False","com.docker.compose.project.config_files":"fixture.yml"}}} for name in ("backend","agent","frontend")}
    monkeypatch.setattr(assets,"docker",lambda *args:" ".join(records).encode())
    monkeypatch.setattr(assets,"inspect",lambda key:records[key])
    with pytest.raises(RuntimeError,match="four"):assets.services("fixture")
    assert set(assets.services("fixture",require_postgres=False))==set(records)


@pytest.mark.parametrize("key,value",[("SPRING_DATASOURCE_URL","jdbc:postgresql://other/wrong"),("SPRING_DATASOURCE_PASSWORD","other"),("SPRING_APPLICATION_JSON",'{}'),("SPRING_CONFIG_ADDITIONAL_LOCATION","/extra.yml"),("JAVA_TOOL_OPTIONS","-Dspring.datasource.url=jdbc:postgresql://other/wrong"),("JAVA_OPTS","--spring.config.location=/other.yml")])
def test_spring_or_launch_target_overrides_are_explicitly_rejected(key,value):
    source=backend();source["Config"]["Env"].append(key+"="+value)
    with pytest.raises(ValueError,match="overrides"):client.target(source)


def test_direct_java_arguments_rejected_but_ordinary_jvm_tls_options_preserved():
    source=backend();source["Config"]["Env"].append("JAVA_TOOL_OPTIONS=-Djavax.net.ssl.trustStore=/run/certs/trust.jks")
    assert client.target(source)["database"]=="actual"
    source["Config"]["Cmd"]=["--spring.datasource.username=other"]
    with pytest.raises(ValueError,match="overrides"):client.target(source)


def test_container_specific_dns_target_override_is_not_silently_ignored():
    source=backend();source["HostConfig"]={"ExtraHosts":["db.example.test:10.0.0.9"]}
    with pytest.raises(ValueError,match="DNS/host"):client.target(source)
