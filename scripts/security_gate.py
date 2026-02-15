from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _collect_ignore_ids(path: Path) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _vulnerability_count(parsed: object) -> int:
    if isinstance(parsed, list):
        deps = parsed
    elif isinstance(parsed, dict):
        deps = parsed.get("dependencies", [])
    else:
        return 0
    count = 0
    if isinstance(deps, list):
        for dep in deps:
            if not isinstance(dep, dict):
                continue
            vulns = dep.get("vulns", [])
            if isinstance(vulns, list):
                count += len(vulns)
    return count


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "artifacts" / "security"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    report_path = out_dir / f"security_gate_{ts}.json"

    py = sys.executable
    req_file = root / "requirements-production.txt"
    ignore_file = root / "security-ignore.txt"
    ignore_ids = _collect_ignore_ids(ignore_file)

    cmd = [py, "-m", "pip_audit", "-f", "json", "-r", str(req_file)]
    for vuln_id in ignore_ids:
        cmd.extend(["--ignore-vuln", vuln_id])

    proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
    rc = proc.returncode
    stdout = proc.stdout
    stderr = proc.stderr

    payload: dict[str, object] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ok": False,
        "command": cmd,
        "rc": rc,
        "stdout": stdout,
        "stderr": stderr,
        "requirements_file": str(req_file),
        "ignore_file": str(ignore_file),
        "ignored_vulns": ignore_ids,
        "vulnerability_count": None,
    }

    try:
        parsed = json.loads(stdout) if stdout.strip() else []
        vuln_count = _vulnerability_count(parsed)
        payload["vulnerability_count"] = vuln_count
        payload["ok"] = (rc == 0 and vuln_count == 0)
        payload["result"] = parsed
    except Exception as e:
        payload["parse_error"] = str(e)
        payload["ok"] = False

    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest_path = out_dir / "security_gate_latest.json"
    latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(str(report_path))
    print("OK" if bool(payload.get("ok")) else "FAIL")
    return 0 if bool(payload.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
