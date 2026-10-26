"""A derived index is rebuildable, but switching datasets must still be explicit."""
import os
from pathlib import Path
import httpx

def verify_marker(folder: Path, identity: str, initialize: bool) -> None:
    marker = folder / ".dataset-id"
    if folder.is_symlink() or marker.is_symlink():
        raise RuntimeError("Dataset markers cannot be symlinks")
    if marker.exists():
        if marker.read_text(encoding="utf-8").strip() != identity:
            raise RuntimeError("Persistent mount belongs to another dataset")
        return
    folder.mkdir(parents=True, exist_ok=True)
    if not initialize or any(folder.iterdir()):
        raise RuntimeError("Persistent mount has no verified dataset marker")
    with marker.open("x", encoding="utf-8") as stream:
        stream.write(identity + "\n")

def verify_dataset(runtime: Path) -> None:
    identity = os.environ.get("DATASET_ID", "")
    if not identity:
        if os.environ.get("APP_ENV") == "production":
            raise RuntimeError("Production requires DATASET_ID")
        return
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        response = client.get(os.environ["BUSINESS_URL"].rstrip("/") + "/internal/v2/dataset",
                              headers={"X-Service-Key": os.environ["INTERNAL_SERVICE_KEY"]})
        response.raise_for_status()
        expected = response.json()
    if expected.get("dataset_id") != identity:
        raise RuntimeError("Business and Agent DATASET_ID differ")
    allow = expected.get("allow_initialize") is True and os.environ.get("DATASET_ENROLL_NEW") == "true"
    verify_marker(runtime, identity, allow)
    verify_marker(Path(os.environ["CHROMA_DIR"]), identity, allow)
