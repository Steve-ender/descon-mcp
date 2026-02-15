from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class StepBase(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str | None = None
    retries: int | None = None
    retry_wait_ms: int | None = None
    wait_after_ms: int | None = None
    require_foreground_match: bool | None = None
    foreground_mismatch_mode: Literal["fail", "refocus_and_retry", "warn"] | None = None
    foreground_retries: int | None = None
    foreground_retry_wait_ms: int | None = None


class WaitStep(StepBase):
    action: Literal["wait"]
    time_ms: int = 0


class MoveMouseStep(StepBase):
    action: Literal["move_mouse"]
    x: int
    y: int
    duration_ms: int = 0


class ClickStep(StepBase):
    action: Literal["click"]
    x: int
    y: int
    button: str = "left"
    clicks: int = 1


class MouseDownStep(StepBase):
    action: Literal["mouse_down"]
    x: int | None = None
    y: int | None = None
    button: str = "left"


class MouseUpStep(StepBase):
    action: Literal["mouse_up"]
    x: int | None = None
    y: int | None = None
    button: str = "left"


class DragToStep(StepBase):
    action: Literal["drag_to"]
    x: int
    y: int
    duration_ms: int = 200
    button: str = "left"
    mouse_down_up: bool = True


class ScrollStep(StepBase):
    action: Literal["scroll"]
    clicks: int
    x: int | None = None
    y: int | None = None
    direction: str = "vertical"


class TypeStep(StepBase):
    action: Literal["type"]
    text: str
    interval_ms: int = 0


class HotkeyStep(StepBase):
    action: Literal["hotkey"]
    keys: list[str]


class KeyPressStep(StepBase):
    action: Literal["key_press"]
    key: str
    presses: int = 1
    interval_ms: int = 0


class FocusWindowStep(StepBase):
    action: Literal["focus_window"]
    title_regex: str | None = None
    window_handle: int | None = None
    window_pid: int | None = None
    timeout_ms: int | None = None


class ListWindowsStep(StepBase):
    action: Literal["list_windows"]
    only_visible: bool = True


class FocusGuardStep(StepBase):
    action: Literal["focus_guard"]
    title_regex: str | None = None
    window_handle: int | None = None
    window_pid: int | None = None
    mismatch_mode: Literal["fail", "refocus_and_retry", "warn"] = "refocus_and_retry"
    retries: int = 3
    retry_wait_ms: int = 150
    resolve_timeout_ms: int = 4000


class LaunchAppStep(StepBase):
    action: Literal["launch_app"]
    command: str
    process_name_for_policy: str | None = None
    shell_mode: bool = False
    cwd: str | None = None


class CloseAppStep(StepBase):
    action: Literal["close_app"]
    pid: int | None = None
    name_filter: str | None = None


class ScreenshotStep(StepBase):
    action: Literal["screenshot"]
    path: str | None = None
    monitor_index: int = 1
    region: dict[str, int] | None = None


class FindTemplateStep(StepBase):
    action: Literal["find_template"]
    template_path: str
    monitor_index: int = 1
    region: dict[str, int] | None = None
    threshold: float = 0.85
    max_results: int = 5
    grayscale: bool = True


class ClickTemplateStep(StepBase):
    action: Literal["click_template"]
    template_path: str
    monitor_index: int = 1
    threshold: float = 0.85
    button: str = "left"
    clicks: int = 1


class ClickElementStep(StepBase):
    action: Literal["click_element", "element_click"]
    window_title_regex: str | None = None
    title: str | None = None
    auto_id: str | None = None
    control_type: str | None = None
    found_index: int = 0
    window_handle: int | None = None
    window_pid: int | None = None
    timeout_ms: int | None = None
    template_path: str | None = None
    text_query: str | None = None
    button: str = "left"
    clicks: int = 1
    monitor_index: int = 1
    threshold: float = 0.85
    region: dict[str, int] | None = None


class TypeElementStep(StepBase):
    action: Literal["type_element", "element_type"]
    window_title_regex: str | None = None
    text: str
    title: str | None = None
    auto_id: str | None = None
    control_type: str | None = None
    found_index: int = 0
    window_handle: int | None = None
    window_pid: int | None = None
    timeout_ms: int | None = None
    clear_first: bool = False
    interval_ms: int = 0
    template_path: str | None = None
    text_query: str | None = None
    monitor_index: int = 1
    threshold: float = 0.85
    region: dict[str, int] | None = None


class ElementActionStep(StepBase):
    action: Literal["element_action"]
    element_action: str = "click"
    window_title_regex: str | None = None
    title: str | None = None
    auto_id: str | None = None
    control_type: str | None = None
    found_index: int = 0
    window_handle: int | None = None
    window_pid: int | None = None
    timeout_ms: int | None = None
    text: str | None = None
    clear_first: bool = False
    allow_fallback: bool = True
    template_path: str | None = None
    text_query: str | None = None
    monitor_index: int = 1
    threshold: float = 0.85
    button: str = "left"
    clicks: int = 1
    interval_ms: int = 0
    region: dict[str, int] | None = None
    fallback_chain: list[str] | None = None


class WaitForStep(StepBase):
    action: Literal["wait_for"]
    condition: str = "exists"
    window_title_regex: str | None = None
    title: str | None = None
    auto_id: str | None = None
    control_type: str | None = None
    found_index: int = 0
    window_handle: int | None = None
    window_pid: int | None = None
    text_query: str | None = None
    timeout_ms: int = 5000
    poll_ms: int = 200


class AssertStep(StepBase):
    action: Literal["assert"]
    condition: str = "exists"
    window_title_regex: str | None = None
    title: str | None = None
    auto_id: str | None = None
    control_type: str | None = None
    found_index: int = 0
    window_handle: int | None = None
    window_pid: int | None = None
    text_query: str | None = None


class ReadTextStep(StepBase):
    action: Literal["read_text"]
    image_path: str
    backend: str = "auto"
    lang: str = "eng"


class AssertTextStep(StepBase):
    action: Literal["assert_text"]
    image_path: str
    contains: str


class TransactionStartStep(StepBase):
    action: Literal["transaction_start"]
    transaction_name: str | None = None
    name_tag: str | None = None
    mode: Literal["fail", "reuse", "restart"] = "fail"


class TransactionCheckpointStep(StepBase):
    action: Literal["transaction_checkpoint"]
    label: str | None = None
    monitor_index: int = 1
    region: dict[str, int] | None = None


class TransactionRollbackHintStep(StepBase):
    action: Literal["transaction_rollback_hint"]
    reason: str | None = None
    max_hints: int = 5


class TransactionEndStep(StepBase):
    action: Literal["transaction_end"]
    committed: bool = True
    summary: str | None = None


class TransactionStatusStep(StepBase):
    action: Literal["transaction_status"]


class IfStep(StepBase):
    action: Literal["if"]
    condition: str
    text: str | None = None
    title_regex: str | None = None
    window_title_regex: str | None = None
    window_handle: int | None = None
    window_pid: int | None = None
    title: str | None = None
    auto_id: str | None = None
    control_type: str | None = None
    found_index: int = 0
    timeout_ms: int | None = None
    monitor_index: int = 1
    then: list[dict[str, Any]] = Field(default_factory=list)
    otherwise: list[dict[str, Any]] = Field(default_factory=list)


class RepeatStep(StepBase):
    action: Literal["repeat"]
    count: int
    steps: list[dict[str, Any]] = Field(default_factory=list)


class WhileStep(StepBase):
    action: Literal["while"]
    condition: str
    text: str | None = None
    title_regex: str | None = None
    window_title_regex: str | None = None
    window_handle: int | None = None
    window_pid: int | None = None
    title: str | None = None
    auto_id: str | None = None
    control_type: str | None = None
    found_index: int = 0
    timeout_ms: int | None = None
    monitor_index: int = 1
    steps: list[dict[str, Any]] = Field(default_factory=list)
    max_iterations: int = 100


StepUnion = Annotated[
    (
        WaitStep
        | MoveMouseStep
        | ClickStep
        | MouseDownStep
        | MouseUpStep
        | DragToStep
        | ScrollStep
        | TypeStep
        | HotkeyStep
        | KeyPressStep
        | FocusWindowStep
        | FocusGuardStep
        | ListWindowsStep
        | LaunchAppStep
        | CloseAppStep
        | ScreenshotStep
        | FindTemplateStep
        | ClickTemplateStep
        | ClickElementStep
        | TypeElementStep
        | ElementActionStep
        | WaitForStep
        | AssertStep
        | ReadTextStep
        | AssertTextStep
        | TransactionStartStep
        | TransactionCheckpointStep
        | TransactionRollbackHintStep
        | TransactionEndStep
        | TransactionStatusStep
        | IfStep
        | RepeatStep
        | WhileStep
    ),
    Field(discriminator="action"),
]


PLAN_ADAPTER = TypeAdapter(list[StepUnion])


def validate_plan(plan: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    models = PLAN_ADAPTER.validate_python(plan)
    validated = [m.model_dump() for m in models]
    return validated, []


def validate_plan_with_errors(plan: list[dict[str, Any]]) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    try:
        validated, _ = validate_plan(plan)
        return validated, []
    except ValidationError as e:
        return None, e.errors(include_url=False)
