from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class StepResult:
    name: str
    ok: bool
    rc: int
    cmd: list[str]
    stdout: str
    stderr: str


def run_step(name: str, cmd: list[str], cwd: Path) -> StepResult:
    env = os.environ.copy()
    root_str = str(cwd)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{root_str}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else root_str
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)
    return StepResult(
        name=name,
        ok=(proc.returncode == 0),
        rc=proc.returncode,
        cmd=cmd,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _bool_env(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    artifacts = root / "artifacts" / "release_gate"
    artifacts.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    report_path = artifacts / f"release_gate_{ts}.json"

    steps: list[StepResult] = []
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    py = str(venv_python) if venv_python.exists() else sys.executable

    strict_security = _bool_env("NOVAFORGE_STRICT_SECURITY_GATE", default=False)
    strict_reliability = _bool_env("NOVAFORGE_STRICT_RELIABILITY_GATE", default=False)
    strict_soak = _bool_env("NOVAFORGE_STRICT_SOAK_GATE", default=False)
    reliability_repeats = os.getenv("NOVAFORGE_RELIABILITY_REPEATS", "10").strip() or "10"
    reliability_preset = os.getenv("NOVAFORGE_RELIABILITY_PRESET", "core").strip() or "core"
    soak_duration_seconds = os.getenv("NOVAFORGE_SOAK_DURATION_SECONDS", "120").strip() or "120"
    soak_workers = os.getenv("NOVAFORGE_SOAK_WORKERS", "3").strip() or "3"
    soak_min_pass_rate = os.getenv("NOVAFORGE_SOAK_MIN_PASS_RATE", "0.99").strip() or "0.99"
    soak_min_runs_per_worker = os.getenv("NOVAFORGE_SOAK_MIN_RUNS_PER_WORKER", "1").strip() or "1"

    steps.append(run_step("compileall", [py, "-m", "compileall", "descon", "scripts", "tests"], cwd=root))
    steps.append(run_step("pytest", [py, "-m", "pytest"], cwd=root))
    steps.append(run_step("production_smoke", [py, "scripts/production_smoke.py"], cwd=root))
    steps.append(run_step("basic_reliable_benchmark", [py, "scripts/basic_reliable_benchmark.py"], cwd=root))
    steps.append(run_step("package_release", [py, "scripts/package_release.py"], cwd=root))
    steps.append(run_step("verify_release_manifest", [py, "scripts/verify_release_manifest.py"], cwd=root))
    steps.append(run_step("generate_sbom", [py, "scripts/generate_sbom.py"], cwd=root))
    if strict_reliability:
        steps.append(
            run_step(
                "reliability_eval",
                [py, "scripts/reliability_eval.py", "--preset", reliability_preset, "--repeats", reliability_repeats],
                cwd=root,
            )
        )
    if strict_soak:
        steps.append(
            run_step(
                "soak_sessions",
                [
                    py,
                    "scripts/soak_sessions.py",
                    "--duration-seconds",
                    soak_duration_seconds,
                    "--workers",
                    soak_workers,
                    "--preset",
                    "core",
                    "--min-pass-rate",
                    soak_min_pass_rate,
                    "--min-runs-per-worker",
                    soak_min_runs_per_worker,
                ],
                cwd=root,
            )
        )

    security_step = run_step("security_gate", [py, "scripts/security_gate.py"], cwd=root)
    if strict_security or security_step.ok:
        steps.append(security_step)
    else:
        steps.append(
            StepResult(
                name="security_gate_non_blocking",
                ok=True,
                rc=security_step.rc,
                cmd=security_step.cmd,
                stdout=security_step.stdout,
                stderr=security_step.stderr,
            )
        )

    policy_checks: list[dict[str, object]] = []
    tests_dir = root / "tests"
    test_files = sorted(p for p in tests_dir.glob("test_*.py"))
    policy_checks.append(
        {
            "name": "tests_exist",
            "ok": len(test_files) >= 4,
            "detail": {"count": len(test_files), "files": [p.name for p in test_files]},
        }
    )

    smoke_report = root / "artifacts" / "smoke" / "production_smoke_report.json"
    smoke_ok = False
    smoke_payload: dict[str, object] = {}
    if smoke_report.exists():
        try:
            smoke_payload = json.loads(smoke_report.read_text(encoding="utf-8"))
            smoke_ok = bool(smoke_payload.get("ok"))
        except Exception as e:
            smoke_payload = {"error": str(e)}
    policy_checks.append(
        {
            "name": "smoke_report_ok",
            "ok": smoke_ok,
            "detail": smoke_payload if smoke_payload else {"exists": smoke_report.exists()},
        }
    )

    bench_dir = root / "artifacts" / "benchmarks"
    bench_reports = sorted(bench_dir.glob("basic_reliable_benchmark_*.json"))
    bench_ok = False
    bench_payload: dict[str, object] = {}
    if bench_reports:
        latest = bench_reports[-1]
        try:
            bench_payload = json.loads(latest.read_text(encoding="utf-8"))
            bench_ok = bool(bench_payload.get("ok"))
            summary = bench_payload.get("summary")
            summary_rows = summary if isinstance(summary, list) else []
            expectation_mode = bool(bench_payload.get("expectation_mode"))
            has_negative_control = any(
                isinstance(row, dict) and (row.get("expected_ok") is False) for row in summary_rows
            )
            bench_ok = bench_ok and expectation_mode and has_negative_control
            bench_payload["expectation_mode"] = expectation_mode
            bench_payload["has_negative_control"] = has_negative_control
            bench_payload["path"] = str(latest)
        except Exception as e:
            bench_payload = {"error": str(e), "path": str(latest)}
    policy_checks.append(
        {
            "name": "benchmark_ok",
            "ok": bench_ok,
            "detail": bench_payload if bench_payload else {"exists": bool(bench_reports)},
        }
    )

    release_manifest = root / "artifacts" / "release" / "package_manifest_latest.json"
    sbom_latest = root / "artifacts" / "release" / "sbom_latest.cyclonedx.json"
    policy_checks.append(
        {
            "name": "package_manifest_exists",
            "ok": release_manifest.exists(),
            "detail": {"path": str(release_manifest), "exists": release_manifest.exists()},
        }
    )
    policy_checks.append(
        {
            "name": "sbom_exists",
            "ok": sbom_latest.exists(),
            "detail": {"path": str(sbom_latest), "exists": sbom_latest.exists()},
        }
    )
    if strict_reliability:
        reliability_latest = root / "artifacts" / "reliability_eval" / "reliability_eval_latest.json"
        reliability_ok = False
        reliability_payload: dict[str, object] = {}
        if reliability_latest.exists():
            try:
                reliability_payload = json.loads(reliability_latest.read_text(encoding="utf-8"))
                reliability_ok = bool(reliability_payload.get("ok"))
            except Exception as e:
                reliability_payload = {"error": str(e), "path": str(reliability_latest)}
        policy_checks.append(
            {
                "name": "reliability_eval_ok",
                "ok": reliability_ok,
                "detail": reliability_payload if reliability_payload else {"exists": reliability_latest.exists(), "path": str(reliability_latest)},
            }
        )
    if strict_soak:
        soak_latest = root / "artifacts" / "soak" / "soak_sessions_latest.json"
        soak_ok = False
        soak_payload: dict[str, object] = {}
        if soak_latest.exists():
            try:
                soak_payload = json.loads(soak_latest.read_text(encoding="utf-8"))
                soak_ok = bool(soak_payload.get("ok"))
            except Exception as e:
                soak_payload = {"error": str(e), "path": str(soak_latest)}
        policy_checks.append(
            {
                "name": "soak_sessions_ok",
                "ok": soak_ok,
                "detail": soak_payload if soak_payload else {"exists": soak_latest.exists(), "path": str(soak_latest)},
            }
        )

    all_steps_ok = all(s.ok for s in steps)
    all_policy_ok = all(bool(c.get("ok")) for c in policy_checks)
    overall_ok = all_steps_ok and all_policy_ok

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ok": overall_ok,
        "strict_security_gate": strict_security,
        "strict_reliability_gate": strict_reliability,
        "strict_soak_gate": strict_soak,
        "reliability_preset": reliability_preset,
        "reliability_repeats": reliability_repeats,
        "soak_duration_seconds": soak_duration_seconds,
        "soak_workers": soak_workers,
        "soak_min_pass_rate": soak_min_pass_rate,
        "soak_min_runs_per_worker": soak_min_runs_per_worker,
        "steps": [
            {
                "name": s.name,
                "ok": s.ok,
                "rc": s.rc,
                "cmd": s.cmd,
                "stdout": s.stdout,
                "stderr": s.stderr,
            }
            for s in steps
        ],
        "policy_checks": policy_checks,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(str(report_path))
    print("OK" if overall_ok else "FAIL")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
