from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


def retry_call(
    func: Callable[[], T],
    *,
    timeout_ms: int = 3000,
    interval_ms: int = 150,
    on_error: Callable[[Exception, int], None] | None = None,
) -> T:
    deadline = time.time() + (max(0, int(timeout_ms)) / 1000.0)
    attempt = 0
    while True:
        attempt += 1
        try:
            return func()
        except Exception as e:
            if on_error is not None:
                try:
                    on_error(e, attempt)
                except Exception:
                    pass
            if time.time() >= deadline:
                raise RuntimeError(f"Timed out after {attempt} attempts: {e}") from e
            time.sleep(max(10, int(interval_ms)) / 1000.0)

