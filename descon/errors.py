from __future__ import annotations

from typing import Any


class DesconError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "runtime_error",
        hint: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.details = details or {}


class ArtifactPathError(DesconError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code="path_missing",
            hint="Ensure the output path is writable and its parent path is valid.",
            details=details,
        )


class ArtifactWriteError(DesconError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code="artifact_write_failed",
            hint="Verify write permissions and free disk space for artifact output.",
            details=details,
        )


class OCRBackendUnavailableError(DesconError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code="ocr_backend_unavailable",
            hint="Install/enable the requested OCR backend or switch to backend=auto.",
            details=details,
        )


class OCRLowConfidenceError(DesconError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code="low_confidence_text",
            hint="Tighten region targeting, retry, or use alternate UIA/template anchors.",
            details=details,
        )


def error_code_for_exception(e: Exception, *, default: str = "runtime_error") -> str:
    if isinstance(e, DesconError):
        return e.code
    return default

