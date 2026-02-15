from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "artifacts" / "release"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    manifest_path = out_dir / f"package_manifest_{ts}.json"

    py = sys.executable
    build_proc = subprocess.run([py, "-m", "build"], cwd=str(root), capture_output=True, text=True)
    if build_proc.returncode != 0:
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": "build_failed",
            "stdout": build_proc.stdout,
            "stderr": build_proc.stderr,
        }
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(str(manifest_path))
        print("FAIL")
        return 1

    dist = root / "dist"
    artifacts = sorted([p for p in dist.iterdir() if p.is_file() and p.suffix in {".whl", ".gz"}])
    if not artifacts:
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": "no_artifacts",
            "build_stdout": build_proc.stdout,
            "build_stderr": build_proc.stderr,
        }
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(str(manifest_path))
        print("FAIL")
        return 1

    rows = []
    for art in artifacts:
        rows.append(
            {
                "filename": art.name,
                "size_bytes": art.stat().st_size,
                "sha256": sha256_file(art),
            }
        )

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ok": True,
        "python": py,
        "artifacts": rows,
        "build_stdout": build_proc.stdout,
        "build_stderr": build_proc.stderr,
    }
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest_path = out_dir / "package_manifest_latest.json"
    latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(str(manifest_path))
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
