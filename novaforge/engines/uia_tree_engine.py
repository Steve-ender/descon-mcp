"""UIA Tree Engine — direct COM access to UI Automation for structured accessibility snapshots.

Uses comtypes to access the Windows UI Automation COM API with CacheRequest batching
and ThreadPoolExecutor parallelism for fast, cross-window element enumeration.
"""
from __future__ import annotations

import atexit
import ctypes
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INTERACTIVE_CONTROL_TYPES = {
    "ButtonControl", "ListItemControl", "MenuItemControl", "EditControl",
    "CheckBoxControl", "RadioButtonControl", "ComboBoxControl", "HyperlinkControl",
    "SplitButtonControl", "TabItemControl", "TreeItemControl", "DataItemControl",
    "HeaderItemControl", "TextBoxControl", "SpinnerControl", "ScrollBarControl",
}

INFORMATIVE_CONTROL_TYPES = {"TextControl", "ImageControl", "StatusBarControl"}

# Maximum children to enumerate per node (prevents runaway traversal)
_MAX_CHILDREN_PER_NODE = 200

# UIA ControlType IDs used for scroll-pattern detection
_UIA_ScrollPatternId = 10004

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class InteractiveElement:
    index: int
    window_name: str
    control_type: str
    name: str
    center_x: int
    center_y: int
    bbox: tuple[int, int, int, int]  # left, top, right, bottom
    is_focused: bool
    value: str = ""


@dataclass
class ScrollableElement:
    index: int
    window_name: str
    control_type: str
    name: str
    center_x: int
    center_y: int
    h_scrollable: bool
    h_percent: float
    v_scrollable: bool
    v_percent: float
    is_focused: bool


@dataclass
class UIASnapshot:
    interactive: list[InteractiveElement] = field(default_factory=list)
    scrollable: list[ScrollableElement] = field(default_factory=list)
    dom_text: list[str] = field(default_factory=list)
    elapsed_ms: int = 0

    def interactive_to_text(self) -> str:
        """Pipe-separated format, token-efficient."""
        if not self.interactive:
            return "No interactive elements"
        header = "# id|window|type|name|coords|focused"
        rows = [header]
        for e in self.interactive:
            rows.append(
                f"{e.index}|{e.window_name}|{e.control_type}|{e.name}"
                f"|({e.center_x},{e.center_y})|{e.is_focused}"
            )
        return "\n".join(rows)

    def scrollable_to_text(self) -> str:
        """Pipe-separated format for scrollable areas."""
        if not self.scrollable:
            return "No scrollable areas"
        header = "# id|window|type|name|coords|h_scroll|h%|v_scroll|v%|focused"
        rows = [header]
        for s in self.scrollable:
            rows.append(
                f"{s.index}|{s.window_name}|{s.control_type}|{s.name}"
                f"|({s.center_x},{s.center_y})|{s.h_scrollable}|{s.h_percent}"
                f"|{s.v_scrollable}|{s.v_percent}|{s.is_focused}"
            )
        return "\n".join(rows)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class UIATreeEngine:
    """Walks the Windows UI Automation tree via COM for structured snapshots."""

    def __init__(self) -> None:
        self._uia: Any | None = None
        self._UIAutomationCore: Any | None = None
        self._cache_request: Any | None = None
        self._com_initialized: bool = False

    # -- lazy COM init ------------------------------------------------------

    def _init_com(self) -> None:
        """Lazy-init: create IUIAutomation COM object on first use."""
        if self._uia is not None:
            return
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        self._com_initialized = True
        atexit.register(self._cleanup_com)
        UIAutomationCore = comtypes.client.GetModule("UIAutomationCore.dll")
        self._uia = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=UIAutomationCore.IUIAutomation,
        )
        self._UIAutomationCore = UIAutomationCore
        self._cache_request = self._create_cache_request()

    def _cleanup_com(self) -> None:
        """Release COM resources on process exit."""
        self._cache_request = None
        self._UIAutomationCore = None
        self._uia = None
        if getattr(self, "_com_initialized", False):
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass
            self._com_initialized = False

    def _create_cache_request(self) -> Any:
        """Build a CacheRequest that batches 12 properties in one cross-process call."""
        uia = self._uia
        cr = uia.CreateCacheRequest()
        # Property IDs (UIA_* constants)
        prop_ids = [
            30005,   # UIA_NamePropertyId
            30011,   # UIA_AutomationIdPropertyId
            30004,   # UIA_LocalizedControlTypePropertyId
            30006,   # UIA_AcceleratorKeyPropertyId
            30012,   # UIA_ClassNamePropertyId
            30003,   # UIA_ControlTypePropertyId
            30010,   # UIA_IsEnabledPropertyId
            30022,   # UIA_IsOffscreenPropertyId
            30016,   # UIA_IsControlElementPropertyId
            30008,   # UIA_HasKeyboardFocusPropertyId
            30009,   # UIA_IsKeyboardFocusablePropertyId
            30001,   # UIA_BoundingRectanglePropertyId
        ]
        for pid in prop_ids:
            try:
                cr.AddProperty(pid)
            except Exception:
                pass
        # TreeScope: subtree
        try:
            cr.TreeScope = 7  # TreeScope_Subtree (Element | Children | Descendants)
        except Exception:
            pass
        return cr

    # -- public API ---------------------------------------------------------

    def snapshot(
        self,
        handles: list[int | dict],
        use_dom: bool = False,
        max_workers: int = 4,
    ) -> UIASnapshot:
        """Main entry point — snapshot multiple windows in parallel.

        Parameters
        ----------
        handles : list
            Window handle ints, or dicts with ``"handle"`` key (from WINDOW_ENGINE.list_windows).
        use_dom : bool
            If True, extract informative text from browser DOM subtrees.
        max_workers : int
            Thread pool size for parallel window traversal.
        """
        self._init_com()
        t0 = time.perf_counter()
        interactive: list[InteractiveElement] = []
        scrollable: list[ScrollableElement] = []
        dom_text: list[str] = []

        # Normalise handles
        raw_handles: list[int] = []
        window_names: dict[int, str] = {}
        for h in handles:
            if isinstance(h, dict):
                raw_handles.append(int(h["handle"]))
                window_names[int(h["handle"])] = h.get("title", "")
            else:
                raw_handles.append(int(h))
                window_names[int(h)] = ""

        if not raw_handles:
            return UIASnapshot(elapsed_ms=0)

        # Screen rect for clipping
        try:
            screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            screen_h = ctypes.windll.user32.GetSystemMetrics(1)
        except Exception:
            screen_w, screen_h = 1920, 1080

        results: list[tuple[list[InteractiveElement], list[ScrollableElement], list[str]]] = []

        def _process_window(handle: int) -> tuple[list[InteractiveElement], list[ScrollableElement], list[str]]:
            import comtypes
            comtypes.CoInitialize()
            try:
                return self._traverse_window(
                    handle,
                    window_name=window_names.get(handle, ""),
                    use_dom=use_dom,
                    screen_w=screen_w,
                    screen_h=screen_h,
                )
            except Exception as exc:
                logger.debug("UIA traverse failed for handle %s: %s", handle, exc)
                return ([], [], [])
            finally:
                comtypes.CoUninitialize()

        if len(raw_handles) == 1:
            results.append(_process_window(raw_handles[0]))
        else:
            workers = min(max_workers, len(raw_handles))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_process_window, h): h for h in raw_handles}
                for fut in as_completed(futures):
                    try:
                        results.append(fut.result())
                    except Exception as exc:
                        logger.debug("UIA worker error for handle %s: %s", futures[fut], exc)

        # Merge and assign global indices
        idx = 0
        for inter, scroll, dom in results:
            for elem in inter:
                elem.index = idx
                idx += 1
            interactive.extend(inter)
        sidx = 0
        for _, scroll, _ in results:
            for s in scroll:
                s.index = sidx
                sidx += 1
            scrollable.extend(scroll)
        for _, _, dom in results:
            dom_text.extend(dom)

        elapsed = int((time.perf_counter() - t0) * 1000)
        return UIASnapshot(
            interactive=interactive,
            scrollable=scrollable,
            dom_text=dom_text,
            elapsed_ms=elapsed,
        )

    # -- per-window traversal -----------------------------------------------

    def _traverse_window(
        self,
        handle: int,
        window_name: str,
        use_dom: bool,
        screen_w: int,
        screen_h: int,
    ) -> tuple[list[InteractiveElement], list[ScrollableElement], list[str]]:
        """Walk the UIA tree for a single window."""
        uia = self._uia
        cr = self._cache_request

        try:
            root = uia.ElementFromHandle(handle)
        except Exception:
            return ([], [], [])

        interactive: list[InteractiveElement] = []
        scrollable: list[ScrollableElement] = []
        dom_text: list[str] = []

        try:
            updated = root.BuildUpdatedCache(cr)
        except Exception:
            updated = root

        self._walk_node(
            updated, cr, window_name, use_dom, False,
            screen_w, screen_h,
            interactive, scrollable, dom_text,
            depth=0, max_depth=30,
        )
        return (interactive, scrollable, dom_text)

    def _walk_node(
        self,
        node: Any,
        cr: Any,
        window_name: str,
        use_dom: bool,
        in_dom: bool,
        screen_w: int,
        screen_h: int,
        interactive: list[InteractiveElement],
        scrollable: list[ScrollableElement],
        dom_text: list[str],
        depth: int,
        max_depth: int,
    ) -> None:
        if depth > max_depth:
            return

        # Extract cached properties safely
        name = _safe_cached(node, "CachedName", "")
        auto_id = _safe_cached(node, "CachedAutomationId", "")
        ctrl_type_str = _safe_cached(node, "CachedLocalizedControlType", "")
        class_name = _safe_cached(node, "CachedClassName", "")
        is_offscreen = _safe_cached(node, "CachedIsOffscreen", True)
        is_control = _safe_cached(node, "CachedIsControlElement", False)
        has_focus = _safe_cached(node, "CachedHasKeyboardFocus", False)
        is_enabled = _safe_cached(node, "CachedIsEnabled", True)

        # Bounding rectangle
        bbox = _safe_bounding_rect(node)
        if bbox is None:
            bbox = (0, 0, 0, 0)
        left, top, right, bottom = bbox

        # Clip to screen
        if right > 0 and bottom > 0:
            left = max(0, left)
            top = max(0, top)
            right = min(screen_w, right)
            bottom = min(screen_h, bottom)

        width = right - left
        height = bottom - top
        center_x = left + width // 2
        center_y = top + height // 2

        visible = not is_offscreen and width > 0 and height > 0

        # DOM detection — enter DOM mode on browser root
        entering_dom = False
        if use_dom and not in_dom and auto_id == "RootWebArea":
            entering_dom = True
            in_dom = True

        # Classify control type using the localized name (e.g. "ButtonControl" → "Button")
        ct_key = ctrl_type_str + "Control" if ctrl_type_str and not ctrl_type_str.endswith("Control") else ctrl_type_str

        # Interactive element?
        if visible and is_control and is_enabled and ct_key in INTERACTIVE_CONTROL_TYPES:
            interactive.append(InteractiveElement(
                index=0,  # assigned later
                window_name=window_name,
                control_type=ctrl_type_str,
                name=name,
                center_x=center_x,
                center_y=center_y,
                bbox=(left, top, right, bottom),
                is_focused=has_focus,
            ))

        # Scrollable element?
        if visible and is_control:
            scroll_info = _safe_scroll_pattern(node, self._UIAutomationCore)
            if scroll_info is not None:
                h_scrollable, h_pct, v_scrollable, v_pct = scroll_info
                scrollable.append(ScrollableElement(
                    index=0,
                    window_name=window_name,
                    control_type=ctrl_type_str,
                    name=name,
                    center_x=center_x,
                    center_y=center_y,
                    h_scrollable=h_scrollable,
                    h_percent=round(h_pct, 1),
                    v_scrollable=v_scrollable,
                    v_percent=round(v_pct, 1),
                    is_focused=has_focus,
                ))

        # Informative DOM text?
        if in_dom and visible and ct_key in INFORMATIVE_CONTROL_TYPES and name:
            dom_text.append(name)

        # Recurse into children
        child_in_dom = in_dom or entering_dom
        try:
            children = node.GetCachedChildren()
            if children is not None:
                count = min(children.Length, _MAX_CHILDREN_PER_NODE)
                for i in range(count):
                    try:
                        child = children.GetElement(i)
                    except Exception:
                        continue
                    self._walk_node(
                        child, cr, window_name, use_dom,
                        child_in_dom,
                        screen_w, screen_h,
                        interactive, scrollable, dom_text,
                        depth + 1, max_depth,
                    )
        except Exception as exc:
            # Fall back to TreeWalker if cached children unavailable
            logger.debug("Cached children unavailable for '%s', falling back to TreeWalker: %s", name, exc)
            try:
                walker = self._uia.ControlViewWalker
                child = walker.GetFirstChildElementBuildCache(node, cr)
                child_count = 0
                while child is not None and child_count < _MAX_CHILDREN_PER_NODE:
                    child_count += 1
                    self._walk_node(
                        child, cr, window_name, use_dom,
                        child_in_dom,
                        screen_w, screen_h,
                        interactive, scrollable, dom_text,
                        depth + 1, max_depth,
                    )
                    try:
                        child = walker.GetNextSiblingElementBuildCache(child, cr)
                    except Exception:
                        break
            except Exception as exc2:
                logger.debug("TreeWalker fallback failed for '%s': %s", name, exc2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_cached(node: Any, attr: str, default: Any) -> Any:
    """Read a cached property from a UIA node, returning *default* on failure."""
    try:
        return getattr(node, attr)
    except Exception:
        return default


def _safe_bounding_rect(node: Any) -> tuple[int, int, int, int] | None:
    """Extract bounding rectangle from cached node.

    Returns (left, top, right, bottom) or None.
    UIA BoundingRectangle is specified as [left, top, width, height] when
    returned as an array/tuple, or as a RECT struct with .left/.top/.right/.bottom.
    """
    try:
        rect = node.CachedBoundingRectangle
        # comtypes may return a RECT/tagRECT struct with named fields
        if hasattr(rect, "left") and hasattr(rect, "right"):
            return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        # UIA spec: array format is always [left, top, width, height]
        if isinstance(rect, (list, tuple)) and len(rect) >= 4:
            l, t, w, h = int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
            if w > 0 and h > 0:
                return (l, t, l + w, t + h)
            return None
        return None
    except Exception:
        return None


def _safe_scroll_pattern(node: Any, uia_core: Any) -> tuple[bool, float, bool, float] | None:
    """Try to get ScrollPattern from a node. Returns (h_scrollable, h%, v_scrollable, v%) or None."""
    try:
        pattern = node.GetCurrentPattern(_UIA_ScrollPatternId)
        if pattern is None:
            return None
        # QI to IUIAutomationScrollPattern
        if uia_core is not None:
            sp = pattern.QueryInterface(uia_core.IUIAutomationScrollPattern)
        else:
            sp = pattern
        h_scrollable = bool(sp.CurrentHorizontallyScrollable)
        h_pct = float(sp.CurrentHorizontalScrollPercent) if h_scrollable else 0.0
        v_scrollable = bool(sp.CurrentVerticallyScrollable)
        v_pct = float(sp.CurrentVerticalScrollPercent) if v_scrollable else 0.0
        if not h_scrollable and not v_scrollable:
            return None
        return (h_scrollable, h_pct, v_scrollable, v_pct)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

UIA_TREE_ENGINE = UIATreeEngine()
