"""Verify backup manifests and propose retention; never delete user backups."""
import argparse
import hashlib
import json
from pathlib import Path


def inspect(root, keep=7):
    root = root.resolve(strict=True)
    results = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink() or not (folder / "manifest.json").is_file():
            continue
        try:
            manifest = json.loads((folder / "manifest.json").read_text())
            hashes = manifest["sha256"]
            if not {"database.dump", "application-data.tar.gz"} <= hashes.keys():
                raise ValueError("Incomplete manifest")
            for name, expected in hashes.items():
                item = (folder / name).resolve(strict=True)
                if not item.is_relative_to(folder.resolve()) or not item.is_file():
                    raise ValueError("Unsafe backup path")
                digest = hashlib.sha256()
                with item.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != expected:
                    raise ValueError("Checksum mismatch")
            results.append({"directory": str(folder), "valid": True, "mtime": folder.stat().st_mtime})
        except (ValueError, KeyError, OSError) as exc:
            results.append({"directory": str(folder), "valid": False, "error": str(exc)})
    valid = sorted([r for r in results if r["valid"]], key=lambda r: r["mtime"], reverse=True)
    for index, row in enumerate(valid):
        row["retention"] = "keep" if index < keep else "eligible_after_restore_verification"
    return {"backups": results, "keep": keep, "deleted": 0,
            "boundary": "Checksum is not a restore test; recommendations only, no automatic deletion."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--keep", type=int, default=7)
    args = parser.parse_args()
    if args.keep < 1:
        parser.error("keep must be positive")
    print(json.dumps(inspect(args.root, args.keep), ensure_ascii=False, indent=2))
