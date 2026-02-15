from __future__ import annotations

from typing import Any

from descon.errors import OCRLowConfidenceError


class VisionPipeline:
    def __init__(self, ocr_engine: Any) -> None:
        self._ocr = ocr_engine

    def read_text_consensus(
        self,
        *,
        image_path: str,
        backend: str,
        lang: str = "eng",
        attempts: int = 3,
        min_consensus: int = 2,
        best_effort_ocr: bool = True,
        fallback_notes: list[str] | None = None,
    ) -> dict[str, Any]:
        data = self._ocr.read_text_stable(
            image_path=image_path,
            backend=backend,
            lang=lang,
            attempts=max(1, int(attempts)),
            min_consensus=max(1, int(min_consensus)),
        )
        if fallback_notes:
            existing = data.get("fallback_notes")
            merged = list(fallback_notes)
            if isinstance(existing, list):
                merged.extend([str(x) for x in existing])
            data["fallback_notes"] = merged
        if bool(data.get("low_confidence")):
            data["warning"] = "low_confidence_text"
            if not best_effort_ocr:
                raise OCRLowConfidenceError(
                    "OCR consensus below configured threshold",
                    details={
                        "consensus_count": data.get("consensus_count"),
                        "attempt_count": data.get("attempt_count"),
                        "min_consensus": max(1, int(min_consensus)),
                    },
                )
        return data

