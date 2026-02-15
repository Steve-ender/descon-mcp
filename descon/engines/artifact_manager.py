from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from descon.errors import ArtifactPathError
from descon.paths import artifacts_root


class ArtifactManager:
    def ensure_parent_dir(self, path: str | Path) -> Path:
        out = Path(path)
        parent = out.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise ArtifactPathError(
                f"Failed to prepare artifact directory: {parent}",
                details={"path": str(out), "parent": str(parent), "cause": str(e)},
            ) from e
        return out

    def resolve_image_output(self, path: str | None = None, prefix: str = "screen_") -> Path:
        if path is None:
            # Sanitize prefix: strip path separators to prevent directory traversal
            safe_prefix = Path(prefix).name.replace("/", "").replace("\\", "") or "screen_"
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            out = artifacts_root() / f"{safe_prefix}{ts}.png"
            return self.ensure_parent_dir(out)
        resolved = Path(path).resolve()
        root = artifacts_root().resolve()
        # Allow paths outside artifacts_root only if explicitly provided by caller
        return self.ensure_parent_dir(resolved)


ARTIFACT_MANAGER = ArtifactManager()

