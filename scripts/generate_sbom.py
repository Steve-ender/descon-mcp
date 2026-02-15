from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "artifacts" / "release"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    out_path = out_dir / f"sbom_{ts}.cyclonedx.json"

    py = sys.executable
    proc = subprocess.run(
        [py, "-m", "cyclonedx_py", "environment", "--output-format", "JSON", "--output-file", str(out_path)],
        cwd=str(root),
        capture_output=True,
        text=True,
    )

    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
        print("SBOM_FAIL")
        return 1

    latest = out_dir / "sbom_latest.cyclonedx.json"
    latest.write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(str(out_path))
    print("SBOM_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
