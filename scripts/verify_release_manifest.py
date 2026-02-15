from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    manifest_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else (root / "artifacts" / "release" / "package_manifest_latest.json")
    manifest_path = manifest_arg if manifest_arg.is_absolute() else (root / manifest_arg)
    if not manifest_path.exists():
        print(f"Manifest not found: {manifest_path}")
        return 1

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, list) or not artifacts:
        print("Manifest missing artifacts list")
        return 1

    dist = root / "dist"
    errors: list[str] = []
    for row in artifacts:
        if not isinstance(row, dict):
            errors.append("artifact row is not an object")
            continue
        filename = row.get("filename")
        expected_sha = row.get("sha256")
        expected_size = row.get("size_bytes")
        if not isinstance(filename, str) or not filename:
            errors.append(f"invalid filename row: {row}")
            continue
        p = dist / filename
        if not p.exists():
            errors.append(f"missing artifact file: {filename}")
            continue
        actual_sha = sha256_file(p)
        actual_size = p.stat().st_size
        if not isinstance(expected_sha, str) or actual_sha.lower() != expected_sha.lower():
            errors.append(f"sha256 mismatch for {filename}")
        if not isinstance(expected_size, int) or actual_size != expected_size:
            errors.append(f"size mismatch for {filename}: expected={expected_size}, actual={actual_size}")

    if errors:
        print("VERIFY_FAIL")
        for e in errors:
            print(e)
        return 1

    print("VERIFY_OK")
    print(str(manifest_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
