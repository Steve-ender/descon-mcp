from __future__ import annotations

import builtins
import importlib

import novaforge.engines.ocr_engine as ocr_engine


def test_rapidocr_init_independent_from_pytesseract_import(monkeypatch):
    real_import = builtins.__import__

    class _FakeRapidOCR:
        def __call__(self, image_path: str):
            return [], None

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pytesseract":
            raise ImportError("pytesseract missing")
        if name == "rapidocr_onnxruntime":
            class _FakeRapidOCRModule:
                RapidOCR = _FakeRapidOCR

            return _FakeRapidOCRModule()
        return real_import(name, globals, locals, fromlist, level)

    with monkeypatch.context() as m:
        m.setattr(builtins, "__import__", _fake_import)
        reloaded = importlib.reload(ocr_engine)
        eng = reloaded.OCREngine()
        assert eng._pytesseract is None
        assert isinstance(eng._import_error, str) and eng._import_error
        assert eng._rapidocr is not None

    importlib.reload(ocr_engine)


def test_read_text_stable_consensus_prefers_majority(monkeypatch):
    eng = ocr_engine.OCREngine()
    samples = iter(
        [
            {"text": "Total: 42", "chars": 9, "backend": "local_model"},
            {"text": "Total: 42", "chars": 9, "backend": "local_model"},
            {"text": "TotaI: 42", "chars": 9, "backend": "local_model"},
        ]
    )
    monkeypatch.setattr(eng, "_read_once", lambda **kwargs: next(samples))

    data = eng.read_text_stable(image_path="x.png", backend="local_model", attempts=3, min_consensus=2)
    assert data["text"] == "Total: 42"
    assert data["consensus_count"] == 2
    assert data["low_confidence"] is False


def test_read_text_stable_flags_low_confidence(monkeypatch):
    eng = ocr_engine.OCREngine()
    samples = iter(
        [
            {"text": "Save", "chars": 4, "backend": "local_model"},
            {"text": "Sove", "chars": 4, "backend": "local_model"},
            {"text": "Sare", "chars": 4, "backend": "local_model"},
        ]
    )
    monkeypatch.setattr(eng, "_read_once", lambda **kwargs: next(samples))

    data = eng.read_text_stable(image_path="x.png", backend="local_model", attempts=3, min_consensus=2)
    assert data["consensus_count"] == 1
    assert data["low_confidence"] is True
