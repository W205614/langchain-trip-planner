"""Read-only OCI Docker-save verification for a Java/Flyway-v2 backup.

Streams every present SHA256 blob, binds the four exact saved image IDs through
catalog/index/manifest/config relationships, and compares ordered config DiffIDs
with docker image inspect. No load, container, removal, tag substitution or Env
output. Non-host and attestation descriptors are reported separately.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import time

PLATFORM = {"os": "linux", "architecture": "amd64"}
JSON_LIMIT = 8 * 1024 * 1024
JSON_BUDGET = 64 * 1024 * 1024
INDEX_TYPES = {"application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json"}
MANIFEST_TYPES = {"application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


class HashingReader:
    def __init__(self, stream):
        self.stream, self.hash, self.bytes = stream, hashlib.sha256(), 0

    def read(self, size=-1):
        value = self.stream.read(size)
        self.hash.update(value); self.bytes += len(value)
        return value


def read_archive(path):
    blobs, documents, metadata, seen = {}, {}, {}, set()
    budget = 0
    with path.open("rb") as source:
        reader = HashingReader(source)
        with tarfile.open(fileobj=reader, mode="r|*") as archive:
            for member in archive:
                name = PurePosixPath(member.name)
                require(not name.is_absolute() and ".." not in name.parts, "unsafe_archive_member")
                key = str(name)
                require(key not in seen, "duplicate_archive_member")
                seen.add(key)
                if member.isdir():
                    continue
                require(member.isfile(), "links_or_special_archive_members_not_supported")
                match = re.fullmatch(r"blobs/sha256/([a-f0-9]{64})", key)
                capture_header = key in {"index.json", "oci-layout"}
                require(not capture_header or member.size <= JSON_LIMIT, "oversized_oci_header")
                value, size, captured = hashlib.sha256(), 0, None
                with archive.extractfile(member) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        if size == 0 and (capture_header or match and member.size <= JSON_LIMIT and block.lstrip().startswith(b"{")):
                            captured = bytearray()
                        if captured is not None:
                            captured.extend(block)
                        value.update(block); size += len(block)
                require(size == member.size, "truncated_archive_member")
                if match:
                    identity = "sha256:" + match.group(1)
                    require(value.hexdigest() == match.group(1), "blob_content_sha256_mismatch")
                    blobs[identity] = {"size": size, "sha256_verified": True}
                if captured is not None:
                    budget += len(captured)
                    require(budget <= JSON_BUDGET, "oci_json_metadata_budget_exceeded")
                    try:
                        parsed = json.loads(captured)
                    except ValueError:
                        require(not capture_header, "invalid_oci_header_json")
                        continue
                    if capture_header:
                        metadata[key] = parsed
                    elif match and isinstance(parsed, dict):
                        # Kept internally only. Never serialize raw config/Env.
                        documents[identity] = parsed
        # Include any final archive padding/trailer in the protected outer hash.
        for _ in iter(lambda: reader.read(1024 * 1024), b""):
            pass
    require("index.json" in metadata and metadata.get("oci-layout", {}).get("imageLayoutVersion") == "1.0.0", "expected_oci_docker_save_layout_missing")
    return blobs, documents, metadata["index.json"], reader.hash.hexdigest(), reader.bytes


def walk_catalog(catalog, blobs, documents):
    manifests, missing, non_host = [], [], []

    def walk(descriptor, ancestors):
        identity = descriptor.get("digest", "")
        require(re.fullmatch(r"sha256:[a-f0-9]{64}", identity) is not None, "unsupported_descriptor_digest")
        require(identity not in ancestors, "cyclic_oci_descriptor_graph")
        declared_size = descriptor.get("size")
        require(isinstance(declared_size, int) and declared_size >= 0, "unknown_descriptor_size")
        path = ancestors + [identity]
        hint = descriptor.get("platform") or {}
        if identity not in blobs:
            record = {"digest": identity, "platform": hint or None, "present": False, "path": path}
            missing.append(record)
            if hint and any(hint.get(key) != value for key, value in PLATFORM.items()):
                non_host.append(record)
            return
        require(blobs[identity]["size"] == declared_size, "descriptor_blob_size_mismatch")
        document = documents.get(identity)
        media = descriptor.get("mediaType")
        if media in INDEX_TYPES:
            require(isinstance(document, dict) and document.get("schemaVersion") == 2 and isinstance(document.get("manifests"), list), "invalid_index_document")
            for nested in document["manifests"]:
                walk(nested, path)
        elif media in MANIFEST_TYPES:
            require(isinstance(document, dict) and document.get("schemaVersion") == 2 and isinstance(document.get("config"), dict) and isinstance(document.get("layers"), list), "invalid_manifest_document")
            config_ref = document["config"]
            config_id = config_ref.get("digest", "")
            require(re.fullmatch(r"sha256:[a-f0-9]{64}", config_id) is not None, "invalid_config_digest")
            config = documents.get(config_id)
            config_present = config_id in blobs
            if config_present:
                require(blobs[config_id]["size"] == config_ref.get("size"), "config_descriptor_size_mismatch")
            platform = {key: (config or {}).get(key, hint.get(key)) for key in PLATFORM}
            is_host = all(platform.get(key) == value for key, value in PLATFORM.items())
            layers = []
            for layer in document["layers"]:
                layer_id, size = layer.get("digest", ""), layer.get("size")
                require(re.fullmatch(r"sha256:[a-f0-9]{64}", layer_id) is not None and isinstance(size, int) and size >= 0, "invalid_layer_descriptor")
                present = layer_id in blobs
                if present:
                    require(blobs[layer_id]["size"] == size, "layer_descriptor_size_mismatch")
                layers.append({"digest": layer_id, "size": size, "present": present,
                               "sha256_verified": present and blobs[layer_id]["sha256_verified"]})
            record = {"manifest": identity, "path": path, "platform": platform,
                      "config": config_id, "config_present": config_present,
                      "config_sha256_verified": config_present and blobs[config_id]["sha256_verified"],
                      "diff_ids": (config or {}).get("rootfs", {}).get("diff_ids"),
                      "rootfs_type": (config or {}).get("rootfs", {}).get("type"),
                      "layers": layers, "is_host": is_host}
            manifests.append(record)
            if not is_host:
                non_host.append({"digest": identity, "config": config_id, "platform": platform,
                                 "present": True, "path": path,
                                 "scope": "non-host/attestation descriptor; no availability or restoration claim"})
        else:
            # Unknown artifacts never establish an image identity or host proof.
            non_host.append({"digest": identity, "platform": hint or None, "present": True,
                             "path": path, "scope": "unselected artifact media type", "media_type": media})

    require(catalog.get("schemaVersion") == 2 and isinstance(catalog.get("manifests"), list), "invalid_archive_catalog")
    for descriptor in catalog["manifests"]:
        walk(descriptor, [])
    return manifests, missing, non_host


def inspect_exact(identity):
    executable = shutil.which("docker")
    require(bool(executable), "docker_executable_unavailable")
    result = subprocess.run([executable, "image", "inspect", identity], capture_output=True, timeout=60)
    require(result.returncode == 0, "exact_local_image_inspect_failed")
    values = json.loads(result.stdout)
    require(len(values) == 1 and values[0].get("Id") == identity, "docker_inspect_exact_image_id_mismatch")
    item = values[0]
    require(item.get("Os") == "linux" and item.get("Architecture") == "amd64", "inspected_image_is_not_requested_host_platform")
    # No Config, Env, CMD, labels, registry auth or RepoTags leaves this function.
    return {"id": identity, "descriptor": item.get("Descriptor"), "os": item.get("Os"),
            "architecture": item.get("Architecture"), "rootfs_type": item.get("RootFS", {}).get("Type"),
            "rootfs_diff_ids": item.get("RootFS", {}).get("Layers")}


def verify(backup, output):
    require(not output.exists(), "fresh_output_required_do_not_overwrite_previous_failure")
    backup = backup.resolve(strict=True)
    report = {"schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(),
              "backup": str(backup), "passed": False, "platform": PLATFORM,
              "images": [], "errors": [], "docker_lifecycle_operations": 0,
              "images_loaded": 0, "containers_created": 0, "deletions": 0,
              "empty_daemon_restore_tested": False,
              "boundary": [
                  "All present OCI blobs are streamed and SHA256/size checked; protected outer images.tar bytes also checked.",
                  "Exact IDs bind through catalog/index/manifest/config references; tags or only equal layers are never identity evidence.",
                  "Config uncompressed DiffID order is compared with local Docker inspect; this is not a new layer-decompression DiffID computation.",
                  "Non-host/attestation missing descriptors remain explicit; no multi-platform or empty-daemon docker load restoration claim.",
                  "Image archive only: remaining PostgreSQL/files/config backup payload verification and runtime readback are separate evidence.",
              ]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    began = time.perf_counter()
    try:
        manifest_path = backup / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(manifest.get("schema") == "Java/Flyway-v2", "expected_v2_backup_manifest")
        images = manifest.get("images", {})
        require(set(images) == {"backend", "agent", "postgres", "frontend"}, "expected_exact_four_service_images")
        require(all(re.fullmatch(r"sha256:[a-f0-9]{64}", identity or "") for identity in images.values()), "non_exact_backup_image_identity")
        archive = (backup / "images.tar").resolve(strict=True)
        require(archive.is_relative_to(backup) and not archive.is_symlink(), "archive_outside_backup")
        expected_archive_hash = manifest.get("sha256", {}).get("images.tar")
        require(re.fullmatch(r"[a-f0-9]{64}", expected_archive_hash or "") is not None, "archive_missing_protected_hash")
        native = {identity: inspect_exact(identity) for identity in dict.fromkeys(images.values())}
        blobs, documents, catalog, archive_hash, archive_size = read_archive(archive)
        require(archive_hash == expected_archive_hash, "protected_archive_sha256_mismatch")
        require(archive_size == archive.stat().st_size, "outer_archive_stream_size_mismatch")
        records, missing, non_host = walk_catalog(catalog, blobs, documents)
        report.update(archive=str(archive), archive_sha256=archive_hash, archive_size_bytes=archive_size,
                      protected_manifest_sha256=digest(manifest_path),
                      verified_blob_count=len(blobs), all_present_blob_sha256_and_sizes_verified=True,
                      missing_descriptors=missing, non_host_descriptors=non_host)
        for identity, inspected in native.items():
            document = documents.get(identity, {})
            if isinstance(document.get("manifests"), list):
                form = "index"
                related = [record for record in records if identity in record["path"]]
            elif isinstance(document.get("config"), dict) and isinstance(document.get("layers"), list):
                form = "manifest"
                related = [record for record in records if record["manifest"] == identity]
            elif isinstance(document.get("rootfs"), dict):
                form = "config"
                related = [record for record in records if record["config"] == identity]
            else:
                raise RuntimeError("exact_image_id_has_no_archive_descriptor_or_config_relationship")
            host = [record for record in related if record["is_host"]]
            selected = [record for record in host if record["diff_ids"] == inspected["rootfs_diff_ids"] and record["rootfs_type"] == inspected["rootfs_type"] == "layers"]
            require(bool(selected), "no_exact_id_host_manifest_with_matching_ordered_config_diffids")
            if form == "index":
                require(not any(identity in item["path"] and item.get("platform") and
                    all(item["platform"].get(key) == value for key, value in PLATFORM.items()) for item in missing),
                    "required_index_host_manifest_missing")
            for record in selected:
                require(record["config_present"] and record["config_sha256_verified"] and
                        all(layer["present"] and layer["sha256_verified"] for layer in record["layers"]),
                        "selected_host_manifest_config_or_layer_closure_missing")
                require(len(record["diff_ids"]) == len(record["layers"]), "config_diffid_layer_count_mismatch")
            report["images"].append({"exact_image_id": identity, "id_form": form,
                "services": [role for role, value in images.items() if value == identity],
                "docker_descriptor": inspected["descriptor"],
                "docker_rootfs_uncompressed_diff_ids": inspected["rootfs_diff_ids"],
                "exact_identity_relationship_verified": True,
                "selected_manifests": [{"digest": record["manifest"], "catalog_descriptor_path": record["path"],
                    "platform": record["platform"], "config": record["config"],
                    "config_diff_ids_order_matches_inspect": True, "layers": record["layers"]} for record in selected],
                "other_host_candidates_not_selected_by_actual_diffids": [record["manifest"] for record in host if record not in selected]})
        report["service_count"] = len(images)
        report["unique_image_count"] = len(native)
        report["passed"] = True
    except BaseException as error:
        report["errors"].append({"error_type": type(error).__name__,
                                 "reason": str(error) if isinstance(error, RuntimeError) else "unexpected_archive_or_metadata_error"})
    report["elapsed_seconds"] = round(time.perf_counter() - began, 3)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = verify(args.backup, args.output)
    except BaseException as error:
        print(json.dumps({"passed": False, "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, RuntimeError) else "invalid_local_arguments"})); return 1
    print(json.dumps({"passed": report["passed"], "output": str(args.output),
                      "verified_blobs": report.get("verified_blob_count"), "images": len(report["images"]),
                      "empty_daemon_restore_tested": False, "docker_lifecycle_operations": 0}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
