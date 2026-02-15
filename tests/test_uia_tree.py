"""Unit tests for UIA tree engine data classes and formatting."""
from __future__ import annotations

from novaforge.engines.uia_tree_engine import (
    InteractiveElement,
    ScrollableElement,
    UIASnapshot,
)


def test_interactive_element_creation():
    elem = InteractiveElement(
        index=0,
        window_name="Notepad",
        control_type="Button",
        name="Save",
        center_x=100,
        center_y=200,
        bbox=(80, 185, 120, 215),
        is_focused=False,
        value="",
    )
    assert elem.index == 0
    assert elem.window_name == "Notepad"
    assert elem.control_type == "Button"
    assert elem.name == "Save"
    assert elem.center_x == 100
    assert elem.center_y == 200
    assert elem.bbox == (80, 185, 120, 215)
    assert elem.is_focused is False


def test_scrollable_element_creation():
    elem = ScrollableElement(
        index=0,
        window_name="Chrome",
        control_type="Pane",
        name="Content",
        center_x=500,
        center_y=400,
        h_scrollable=False,
        h_percent=0.0,
        v_scrollable=True,
        v_percent=35.5,
        is_focused=True,
    )
    assert elem.v_scrollable is True
    assert elem.v_percent == 35.5
    assert elem.h_scrollable is False


def test_empty_snapshot_interactive_text():
    snap = UIASnapshot()
    assert snap.interactive_to_text() == "No interactive elements"


def test_empty_snapshot_scrollable_text():
    snap = UIASnapshot()
    assert snap.scrollable_to_text() == "No scrollable areas"


def test_interactive_to_text_pipe_format():
    elems = [
        InteractiveElement(
            index=0, window_name="Notepad", control_type="Button",
            name="Save", center_x=100, center_y=200,
            bbox=(80, 185, 120, 215), is_focused=False,
        ),
        InteractiveElement(
            index=1, window_name="Notepad", control_type="Edit",
            name="Text Editor", center_x=300, center_y=400,
            bbox=(50, 250, 550, 550), is_focused=True,
        ),
    ]
    snap = UIASnapshot(interactive=elems, elapsed_ms=42)
    text = snap.interactive_to_text()
    lines = text.strip().split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("# id|window|type|name|coords|focused")
    assert "0|Notepad|Button|Save|(100,200)|False" in lines[1]
    assert "1|Notepad|Edit|Text Editor|(300,400)|True" in lines[2]


def test_scrollable_to_text_pipe_format():
    elems = [
        ScrollableElement(
            index=0, window_name="Chrome", control_type="Pane",
            name="Content", center_x=500, center_y=400,
            h_scrollable=False, h_percent=0.0,
            v_scrollable=True, v_percent=35.5, is_focused=True,
        ),
    ]
    snap = UIASnapshot(scrollable=elems, elapsed_ms=10)
    text = snap.scrollable_to_text()
    lines = text.strip().split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("# id|window|type|name|coords")
    assert "0|Chrome|Pane|Content|(500,400)|False|0.0|True|35.5|True" in lines[1]


def test_snapshot_with_dom_text():
    snap = UIASnapshot(
        dom_text=["Hello World", "Click here"],
        elapsed_ms=5,
    )
    assert snap.dom_text == ["Hello World", "Click here"]
    assert snap.interactive_to_text() == "No interactive elements"


def test_snapshot_elapsed_ms():
    snap = UIASnapshot(elapsed_ms=123)
    assert snap.elapsed_ms == 123
