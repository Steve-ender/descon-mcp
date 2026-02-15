from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from descon.engines.screen_engine import SCREEN_ENGINE
from descon.paths import artifacts_subdir
from descon.state import STATE


class TransactionEngine:
    def _base_dir(self) -> Path:
        return artifacts_subdir("transactions")

    def _current_dir(self, session_id: str | None = None) -> Path:
        st = STATE.get(session_id=session_id)
        tx_id = st.transaction_id
        if not tx_id:
            raise RuntimeError("No active transaction id")
        p = self._base_dir() / tx_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _sha256(self, path: str) -> str:
        h = hashlib.sha256()
        with Path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def start(self, name: str | None = None, mode: str = "fail", session_id: str | None = None) -> dict[str, Any]:
        st_existing = STATE.get(session_id=session_id)
        behavior = mode.strip().lower()
        if behavior not in {"fail", "reuse", "restart"}:
            raise RuntimeError("Unsupported transaction start mode. Use fail, reuse, or restart.")

        if st_existing.transaction_active:
            if behavior == "fail":
                raise RuntimeError("Transaction already active. Use mode=reuse or mode=restart.")
            if behavior == "reuse":
                tx_id = st_existing.transaction_id or "unknown"
                tx_dir = self._base_dir() / tx_id
                tx_dir.mkdir(parents=True, exist_ok=True)
                return {
                    "transaction_id": tx_id,
                    "name": st_existing.transaction_name,
                    "started_at": st_existing.transaction_started_at,
                    "artifact_dir": str(tx_dir),
                    "status": "active_reused",
                }
            # restart mode: close existing then create new
            self.end(committed=False, summary="Restarted by transaction_start(mode=restart)", session_id=session_id)

        tx_id = datetime.now(timezone.utc).strftime("tx_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        st = STATE.transaction_start(tx_id=tx_id, name=name, session_id=session_id)
        tx_dir = self._base_dir() / tx_id
        tx_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "transaction_id": tx_id,
            "name": name,
            "started_at": st.transaction_started_at,
            "status": "active",
            "checkpoints": [],
            "hints": [],
        }
        (tx_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return {
            "transaction_id": tx_id,
            "name": name,
            "started_at": st.transaction_started_at,
            "artifact_dir": str(tx_dir),
            "status": "active",
        }

    def status(self, session_id: str | None = None) -> dict[str, Any]:
        st = STATE.get(session_id=session_id)
        return {
            "transaction_active": st.transaction_active,
            "transaction_id": st.transaction_id,
            "transaction_name": st.transaction_name,
            "transaction_started_at": st.transaction_started_at,
            "transaction_committed": st.transaction_committed,
            "transaction_summary": st.transaction_summary,
            "checkpoint_count": len(st.transaction_checkpoints),
            "hint_count": len(st.transaction_hints),
            "checkpoints": st.transaction_checkpoints,
            "hints": st.transaction_hints,
        }

    def checkpoint(
        self,
        label: str | None = None,
        monitor_index: int = 1,
        region: dict[str, int] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        st = STATE.get(session_id=session_id)
        if not st.transaction_active:
            raise RuntimeError("No active transaction. Call desktop_transaction_start first.")
        tx_dir = self._current_dir(session_id=session_id)
        # Read fresh checkpoint count right before building the entry
        idx = len(STATE.get(session_id=session_id).transaction_checkpoints) + 1
        name = label or f"checkpoint_{idx:03d}"
        path = str(tx_dir / f"{idx:03d}_{name}.png")
        shot = SCREEN_ENGINE.capture(path=path, monitor_index=monitor_index, region=region)
        digest = self._sha256(shot["path"])
        entry = {
            "index": idx,
            "label": name,
            "ts": datetime.now(timezone.utc).isoformat(),
            "path": shot["path"],
            "sha256": digest,
            "width": shot["width"],
            "height": shot["height"],
            "monitor_index": monitor_index,
            "region": region,
        }
        STATE.transaction_add_checkpoint(entry, session_id=session_id)
        return entry

    def rollback_hint(self, reason: str | None = None, max_hints: int = 5, session_id: str | None = None) -> dict[str, Any]:
        st = STATE.get(session_id=session_id)
        if not st.transaction_active and not st.transaction_checkpoints:
            raise RuntimeError("No transaction context available")
        checkpoints = st.transaction_checkpoints[-max(1, max_hints) :]
        hints: list[dict[str, Any]] = []
        if checkpoints:
            latest = checkpoints[-1]
            hints.append(
                {
                    "kind": "visual_restore",
                    "message": "Use latest checkpoint screenshot to restore UI state manually or with template matching.",
                    "checkpoint_path": latest.get("path"),
                    "checkpoint_sha256": latest.get("sha256"),
                }
            )
        if st.focused_window_title:
            hints.append(
                {
                    "kind": "focus_window",
                    "message": "Refocus previous active window before replay.",
                    "title": st.focused_window_title,
                }
            )
        hints.append(
            {
                "kind": "replay_safe_prefix",
                "message": "Replay from last known safe checkpoint using idempotent subset of steps.",
                "checkpoint_count": len(st.transaction_checkpoints),
            }
        )
        if reason:
            hints.append({"kind": "reason", "message": reason})

        detail = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "hints": hints,
        }
        STATE.transaction_add_hint(detail, session_id=session_id)
        return {"reason": reason, "hints": hints, "checkpoint_count": len(st.transaction_checkpoints)}

    def end(self, committed: bool = True, summary: str | None = None, session_id: str | None = None) -> dict[str, Any]:
        st = STATE.get(session_id=session_id)
        tx_id = st.transaction_id
        tx_dir = self._base_dir() / tx_id if tx_id else None
        updated = STATE.transaction_end(committed=committed, summary=summary, session_id=session_id)
        data = {
            "transaction_id": tx_id,
            "committed": bool(committed),
            "summary": summary,
            "checkpoint_count": len(updated.transaction_checkpoints),
            "hint_count": len(updated.transaction_hints),
            "ended_at": datetime.now(timezone.utc).isoformat(),
        }
        if tx_dir and tx_dir.exists():
            manifest_path = tx_dir / "manifest.json"
            payload = {}
            if manifest_path.exists():
                try:
                    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    payload = {}
            payload.update(
                {
                    "status": "committed" if committed else "aborted",
                    "ended_at": data["ended_at"],
                    "summary": summary,
                    "checkpoints": updated.transaction_checkpoints,
                    "hints": updated.transaction_hints,
                }
            )
            manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            data["manifest_path"] = str(manifest_path)
        return data

    def export(self, path: str | None = None, session_id: str | None = None) -> dict[str, Any]:
        st = STATE.get(session_id=session_id)
        if not st.transaction_id:
            raise RuntimeError("No transaction id to export")
        payload = self.status(session_id=session_id)
        payload["exported_at"] = datetime.now(timezone.utc).isoformat()
        if path is None:
            tx_dir = self._base_dir() / st.transaction_id
            tx_dir.mkdir(parents=True, exist_ok=True)
            path = str(tx_dir / "export.json")
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return {"path": path, "transaction_id": st.transaction_id}


TRANSACTION_ENGINE = TransactionEngine()
