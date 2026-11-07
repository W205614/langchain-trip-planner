"""Check public OpenAPI routes, Java request records and response allowlists.

Reads the full repository only; does not initialize either service or call a provider.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator
import yaml

ROOT = Path(__file__).resolve().parents[2]
JAVA = ROOT / "business-backend/src/main/java/com/tripplanner"
CONTRACT = ROOT / "contracts/public-operations.openapi.yaml"
META_SCHEMA = ROOT / "contracts/openapi-3.1.schema.json"
META_SCHEMA_SHA256 = "da01ba28852cac0de53893797cb8d1942bc3b05084f526dcc216717dec314ed0"
CONTROLLERS = (
    "EmailAccountController", "MailAdminController", "PublicOperationsController",
    "AccountDataController", "StorageController",
)


def reference(document: dict, value: dict) -> dict:
    while "$ref" in value:
        target = value["$ref"]
        if not target.startswith("#/"):
            raise AssertionError(f"Unexpected external reference: {target}")
        value = document
        for name in target[2:].split("/"):
            value = value[name]
    return value


def mappings(source: str) -> set[tuple[str, str]]:
    prefix = source[:source.index("public class")]
    base = re.search(r'@RequestMapping\("([^\"]*)"\)', prefix)
    base = base.group(1) if base else ""
    result = set()
    for match in re.finditer(r"@(Get|Post|Put|Delete|Patch)Mapping(?:\(([^)]*)\))?", source):
        argument = match.group(2) or ""
        values = re.findall(r'"([^\"]*)"', argument)
        if not values:
            values = [""]
        result.update((method, base + path) for path in values for method in [match.group(1).lower()])
    return result


def request_record(source: str, name: str) -> tuple[set[str], set[str]]:
    match = re.search(rf"public record {re.escape(name)}\((.*?)\)\s*\{{", source, re.S)
    if match is None:
        raise AssertionError(f"Java request record disappeared: {name}")
    fields, required = set(), set()
    # Remove commas inside annotations before splitting Java record parameters.
    parameters = re.sub(r"(@\w+)\([^)]*\)", r"\1", match.group(1)).split(",")
    for parameter in parameters:
        field = re.search(r"(\w+)\s*$", parameter).group(1)
        fields.add(field)
        if "@NotNull" in parameter or "@NotBlank" in parameter:
            required.add(field)
    return fields, required


def validate_contract(path: Path = CONTRACT, openapi_schema: Path = META_SCHEMA) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["openapi"] == "3.1.0"
    schemas = document["components"]["schemas"]
    paths = document["paths"]
    actual = {
        (method, path)
        for path, item in paths.items()
        for method in item
        if method in {"get", "post", "put", "delete", "patch"}
    }
    for controller in CONTROLLERS:
        expected = mappings((JAVA / "api" / f"{controller}.java").read_text(encoding="utf-8"))
        assert expected <= actual, f"Undocumented endpoints in {controller}: {sorted(expected - actual)}"
    users = mappings((JAVA / "api/UsersController.java").read_text(encoding="utf-8"))
    assert all(pair in users for pair in actual if pair[1] in {"/api/auth/register", "/api/auth/login", "/api/auth/logout", "/api/auth/me"})
    known = set(users)
    for controller in CONTROLLERS:
        known |= mappings((JAVA / "api" / f"{controller}.java").read_text(encoding="utf-8"))
    assert actual <= known, f"Contract invents endpoints: {sorted(actual - known)}"

    operations = set()
    for path, item in paths.items():
        for method, operation in item.items():
            if method not in {"get", "post", "put", "delete", "patch"}:
                continue
            assert operation["operationId"] not in operations, "Duplicate operationId"
            operations.add(operation["operationId"])
            parameters = [reference(document, p) for p in operation.get("parameters", [])]
            declared = {p["name"] for p in parameters if p["in"] == "path" and p.get("required") is True}
            assert declared == set(re.findall(r"\{(\w+)\}", path)), f"Incorrect path parameters: {path}"
            if method in {"post", "put", "delete", "patch"} and operation.get("security") != []:
                assert any(p["name"] == "X-CSRF-Token" for p in parameters), f"Missing Cookie CSRF contract: {path}"
            if path.startswith(("/api/admin/", "/api/knowledge/admin/")):
                assert operation.get("x-required-role") == "ADMIN", f"Missing admin policy: {path}"

    records = 0
    for name, schema in schemas.items():
        Draft202012Validator.check_schema(schema)
        if "x-java-record" in schema:
            controller, record = schema["x-java-record"].split(".")
            fields, required = request_record((JAVA / "api" / f"{controller}.java").read_text(encoding="utf-8"), record)
            assert fields == set(schema["properties"]), f"Request fields drifted: {name}"
            assert required == set(schema["required"]), f"Required request fields drifted: {name}"
            records += 1

    allowlists = {
        "AdminUser": ("domain/PublicOperationsService.java", "users"),
        "Feedback": ("domain/PublicOperationsService.java", "user_feedback"),
        "MailDelivery": ("security/MailDeliveryService.java", "mail_deliveries"),
        "DataJob": ("domain/AccountDataService.java", "account_data_jobs"),
        "AdminDataJob": ("domain/AccountDataService.java", "account_data_jobs"),
    }
    for name, (filename, table) in allowlists.items():
        source = (JAVA / filename).read_text(encoding="utf-8")
        selections = [set(columns.split(",")) for columns in re.findall(rf"SELECT\s+([a-z_,]+)\s+FROM\s+{table}\b", source)]
        assert set(schemas[name]["properties"]) in selections, f"Response SQL allowlist drifted: {name}"
    access = (JAVA / "security/AccountAccess.java").read_text(encoding="utf-8")
    fields = re.search(r"for \(String field : Set\.of\((.*?)\)\)", access, re.S).group(1)
    public = set(re.findall(r'"([^\"]+)"', fields)) | {"email_verified"}
    assert public == set(schemas["PublicUser"]["properties"]), "Public account response allowlist drifted"

    quota = (JAVA / "domain/UserQuotaService.java").read_text(encoding="utf-8")
    defaults = re.search(r"this\.defaults=Map\.of\((.*?)\);", quota, re.S).group(1)
    quota_fields = set(re.findall(r'"(\w+)"\s*,', defaults))
    assert quota_fields == set(schemas["QuotaUpdate"]["properties"]) == set(schemas["QuotaUpdate"]["required"])
    assert quota_fields == set(schemas["QuotaValues"]["properties"])

    def refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                reference(document, value)
            for child in value.values():
                refs(child)
        elif isinstance(value, list):
            for child in value:
                refs(child)
    refs(document)
    schema_bytes = openapi_schema.read_bytes()
    if openapi_schema.resolve() == META_SCHEMA.resolve():
        assert hashlib.sha256(schema_bytes).hexdigest() == META_SCHEMA_SHA256, "Pinned upstream OpenAPI meta-schema changed"
    Draft202012Validator(json.loads(schema_bytes)).validate(document)
    return {"passed": True, "paths": len(paths), "operations": len(operations), "schemas": len(schemas),
            "java_request_records": records, "response_allowlists": len(allowlists) + 1,
            "openapi_meta_schema_checked": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=CONTRACT)
    parser.add_argument("--openapi-schema", type=Path, default=META_SCHEMA, help="Pinned OpenAPI 3.1 meta-schema for offline validation")
    options = parser.parse_args()
    print(json.dumps(validate_contract(options.contract, options.openapi_schema), ensure_ascii=False))
