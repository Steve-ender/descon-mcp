from __future__ import annotations

from pathlib import Path
from typing import Any
import math
import re

from PIL import Image
import shutil
from descon.errors import ArtifactPathError, OCRBackendUnavailableError


class OCREngine:
    def __init__(self) -> None:
        self._pytesseract = None
        self._import_error = None
        self._rapidocr = None
        self._rapidocr_import_error = None

        try:
            import pytesseract  # type: ignore
            self._pytesseract = pytesseract
            if shutil.which("tesseract") is None:
                default_win_path = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
                if default_win_path.exists():
                    self._pytesseract.pytesseract.tesseract_cmd = str(default_win_path)
        except Exception as e:  # pragma: no cover
            self._import_error = str(e)

        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore

            self._rapidocr = RapidOCR()
        except Exception as e:  # pragma: no cover
            self._rapidocr_import_error = str(e)

    def read_text_tesseract(self, image_path: str, lang: str = "eng") -> dict[str, Any]:
        if self._pytesseract is None:
            raise OCRBackendUnavailableError(
                f"pytesseract unavailable: {self._import_error}",
                details={"backend": "tesseract"},
            )

        p = Path(image_path)
        if not p.exists():
            raise ArtifactPathError(f"Image not found: {image_path}", details={"image_path": image_path})

        with Image.open(image_path) as image:
            text = self._pytesseract.image_to_string(image, lang=lang)
        return {"text": text, "chars": len(text), "backend": "tesseract", "confidence": None}

    def read_text_rapidocr(self, image_path: str) -> dict[str, Any]:
        if self._rapidocr is None:
            raise OCRBackendUnavailableError(
                f"rapidocr unavailable: {self._rapidocr_import_error}",
                details={"backend": "local_model"},
            )

        p = Path(image_path)
        if not p.exists():
            raise ArtifactPathError(f"Image not found: {image_path}", details={"image_path": image_path})

        result, _ = self._rapidocr(str(p))
        if not result:
            return {"text": "", "chars": 0, "backend": "rapidocr", "confidence": 0.0}

        lines: list[str] = []
        confidences: list[float] = []
        for item in result:
            # RapidOCR item shape: [box_points, text, score]
            if len(item) >= 3:
                lines.append(str(item[1]))
                try:
                    confidences.append(float(item[2]))
                except Exception:
                    pass

        text = "\n".join(lines)
        confidence = sum(confidences) / len(confidences) if confidences else None
        return {"text": text, "chars": len(text), "backend": "rapidocr", "confidence": confidence}

    def read_text_local_model(self, image_path: str) -> dict[str, Any]:
        data = self.read_text_rapidocr(image_path=image_path)
        data["backend"] = "local_model"
        return data

    @staticmethod
    def _normalize_text(text: str) -> str:
        # Normalize whitespace/casing so minor OCR noise does not fragment consensus votes.
        collapsed = re.sub(r"\s+", " ", str(text or "")).strip().lower()
        return collapsed

    def _read_once(self, image_path: str, backend: str, lang: str = "eng") -> dict[str, Any]:
        selected = str(backend or "auto").strip().lower()
        if selected == "rapidocr":
            selected = "local_model"
        if selected == "local_model":
            return self.read_text_local_model(image_path=image_path)
        if selected == "tesseract":
            return self.read_text_tesseract(image_path=image_path, lang=lang)
        if selected == "auto":
            notes: list[str] = []
            try:
                out = self.read_text_local_model(image_path=image_path)
                if int(out.get("chars", 0)) > 0:
                    out["fallback_notes"] = notes
                    return out
                notes.append("local_model returned empty text")
            except Exception as e:
                notes.append(f"local_model failed: {e}")
            out = self.read_text_tesseract(image_path=image_path, lang=lang)
            out["fallback_notes"] = notes
            return out
        raise RuntimeError(f"Unsupported OCR backend: {backend}")

    def read_text_stable(
        self,
        *,
        image_path: str,
        backend: str = "auto",
        lang: str = "eng",
        attempts: int = 3,
        min_consensus: int = 2,
    ) -> dict[str, Any]:
        count = max(1, int(attempts))
        threshold = max(1, int(min_consensus))
        runs: list[dict[str, Any]] = []
        errors: list[str] = []
        exceptions: list[Exception] = []
        votes: dict[str, int] = {}
        best_for_key: dict[str, dict[str, Any]] = {}

        for _ in range(count):
            try:
                sample = self._read_once(image_path=image_path, backend=backend, lang=lang)
                runs.append(sample)
                key = self._normalize_text(str(sample.get("text", "")))
                votes[key] = votes.get(key, 0) + 1
                incumbent = best_for_key.get(key)
                if incumbent is None or int(sample.get("chars", 0)) > int(incumbent.get("chars", 0)):
                    best_for_key[key] = sample
            except Exception as e:
                errors.append(str(e))
                exceptions.append(e)

        if not runs:
            if exceptions:
                first = exceptions[0]
                raise first
            raise RuntimeError("OCR failed with no result")

        winner_key = ""
        winner_count = -1
        winner_chars = -1
        for key, vote_count in votes.items():
            chars = int(best_for_key.get(key, {}).get("chars", 0))
            if vote_count > winner_count or (vote_count == winner_count and chars > winner_chars):
                winner_key = key
                winner_count = vote_count
                winner_chars = chars

        winner = dict(best_for_key.get(winner_key, runs[0]))
        winner["consensus_count"] = max(0, winner_count)
        winner["attempt_count"] = count
        winner["successful_attempts"] = len(runs)
        winner["low_confidence"] = winner_count < threshold
        winner["errors"] = errors
        return winner

    def find_text_local_model(self, image_path: str, query: str, case_sensitive: bool = False) -> dict[str, Any]:
        if self._rapidocr is None:
            raise OCRBackendUnavailableError(
                f"rapidocr unavailable: {self._rapidocr_import_error}",
                details={"backend": "local_model"},
            )

        p = Path(image_path)
        if not p.exists():
            raise ArtifactPathError(f"Image not found: {image_path}", details={"image_path": image_path})

        result, _ = self._rapidocr(str(p))
        matches: list[dict[str, Any]] = []
        if not result:
            return {"query": query, "count": 0, "matches": []}

        q = query if case_sensitive else query.lower()
        for item in result:
            if len(item) < 3:
                continue
            points = item[0] or []
            text = str(item[1])
            score = float(item[2])
            text_cmp = text if case_sensitive else text.lower()
            if q not in text_cmp:
                continue
            xs = [int(p[0]) for p in points] if points else [0]
            ys = [int(p[1]) for p in points] if points else [0]
            left, right = min(xs), max(xs)
            top, bottom = min(ys), max(ys)
            matches.append(
                {
                    "text": text,
                    "score": score,
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                    "center_x": int((left + right) / 2),
                    "center_y": int((top + bottom) / 2),
                }
            )
        matches.sort(key=lambda m: float(m.get("score", 0.0)), reverse=True)
        return {"query": query, "count": len(matches), "matches": matches}

    def select_text_target_local_model(
        self,
        image_path: str,
        query: str,
        case_sensitive: bool = False,
        near_x: int | None = None,
        near_y: int | None = None,
    ) -> dict[str, Any]:
        found = self.find_text_local_model(
            image_path=image_path,
            query=query,
            case_sensitive=case_sensitive,
        )
        matches = found["matches"]
        if not matches:
            return {"query": query, "count": 0, "best": None, "matches": []}

        q_cmp = query if case_sensitive else query.lower()
        ranked: list[dict[str, Any]] = []
        for m in matches:
            text = str(m.get("text", ""))
            t_cmp = text if case_sensitive else text.lower()

            lexical = 0.0
            if t_cmp == q_cmp:
                lexical = 1.0
            elif t_cmp.startswith(q_cmp):
                lexical = 0.85
            elif q_cmp in t_cmp:
                lexical = 0.65

            conf = float(m.get("score", 0.0))
            spatial = 0.5
            if near_x is not None and near_y is not None:
                dx = float(m.get("center_x", 0) - near_x)
                dy = float(m.get("center_y", 0) - near_y)
                dist = math.sqrt(dx * dx + dy * dy)
                # Normalize by 600 px distance scale; close matches approach 1.0.
                spatial = max(0.0, 1.0 - min(1.0, dist / 600.0))

            total = 0.55 * lexical + 0.35 * conf + 0.10 * spatial
            ranked.append({**m, "rank_lexical": lexical, "rank_conf": conf, "rank_spatial": spatial, "rank_total": total})

        ranked.sort(key=lambda x: float(x.get("rank_total", 0.0)), reverse=True)
        return {"query": query, "count": len(ranked), "best": ranked[0], "matches": ranked}


OCR_ENGINE = OCREngine()
