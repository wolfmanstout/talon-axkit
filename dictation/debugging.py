import time
import traceback
from dataclasses import replace

from talon import Module, actions, cron, noise, settings, ui
from talon.types import Span

from .ax_tree_text import chromium_text_model_debug_lines
from .dictation_context import (
    DEFAULT_CONTEXT_CHARACTERS,
    AccessibilityContext,
)

try:
    from talon.ui import Element
except ImportError:
    Element = type(None)

HISS_DEBUG_ENABLED = True

mod = Module()
mod.setting(
    "hiss_to_debug_accessibility",
    type=bool,
    default=False,
    desc="Use a hissing sound to print accessibility debugging information to the Talon log.",
)
mod.setting(
    "hiss_to_debug_accessibility_threshold",
    type=float,
    default=0.35,
    desc="If hiss_to_debug_accessibility is enabled, the hissing duration (in seconds) needed to trigger the debug output.",
)


def _safe_get(el, attr):
    try:
        return el.get(attr)
    except Exception as error:
        return f"<{type(error).__name__}: {error}>"


def _text_excerpt(text, index=None, radius=DEFAULT_CONTEXT_CHARACTERS):
    if text is None:
        return "None"

    if index is None:
        start = 0
        end = min(len(text), radius * 2)
    else:
        start = max(0, index - radius)
        end = min(len(text), index + radius)

    prefix = "..." if start else ""
    suffix = "..." if end < len(text) else ""
    return prefix + repr(text[start:end]) + suffix


def _span_text_window(label, content, selection, radius):
    if content is None:
        return [f"{label}: content=None"]
    if selection is None:
        return [f"{label}: selection=None content_len={len(content)}"]

    if selection.left == selection.right:
        return [
            f"{label}: content_len={len(content)} selection={selection}",
            f"  caret_window={_text_excerpt(content, selection.left, radius)}",
        ]

    return [
        f"{label}: content_len={len(content)} selection={selection}",
        f"  window_around_selection_start={_text_excerpt(content, selection.left, radius)}",
        f"  selected={repr(content[selection.left : selection.right])}",
        f"  window_around_selection_end={_text_excerpt(content, selection.right, radius)}",
    ]


def _dictation_peek_lines(context, radius):
    if context.content is None or context.selection is None:
        return ["dictation_peek equivalent: accessibility fallback"]

    return [
        "dictation_peek equivalent:",
        f"  left_context={context.left_context(radius)!r}",
        f"  right_context={context.right_context(radius)!r}",
    ]


def _raw_context_from_element(el):
    selection = _safe_get(el, "AXSelectedTextRange")
    if selection is None or not hasattr(selection, "a"):
        selection = Span(0, 0)

    shared_range = _safe_get(el, "AXSharedCharacterRange")
    if shared_range and hasattr(shared_range, "a"):
        selection = Span(selection.a - shared_range.a, selection.b - shared_range.a)

    return AccessibilityContext(content=_safe_get(el, "AXValue"), selection=selection)


def _element_summary(el):
    fields = [
        "AXRole",
        "AXSubrole",
        "AXTitle",
        "AXDescription",
        "AXValue",
        "AXSelectedTextRange",
        "AXSharedCharacterRange",
        "AXNumberOfCharacters",
        "AXVisibleCharacterRange",
        "AXInsertionPointLineNumber",
    ]
    lines = [f"Focused element: {el}"]
    for field in fields:
        value = _safe_get(el, field)
        if field == "AXValue":
            lines.append(
                f"  {field}: len={len(value) if isinstance(value, str) else 'n/a'} "
                f"{_text_excerpt(value, radius=120)}"
            )
        else:
            lines.append(f"  {field}: {value!r}")
    return lines


def _tree_lines(el, max_depth=8, max_nodes=220, text_limit=100):
    lines = ["AX tree:"]
    remaining = {"nodes": max_nodes}

    def walk(node, depth):
        if remaining["nodes"] <= 0:
            return
        remaining["nodes"] -= 1

        role = _safe_get(node, "AXRole")
        value = _safe_get(node, "AXValue")
        description = _safe_get(node, "AXDescription")
        title = _safe_get(node, "AXTitle")
        parts = [f"role={role!r}"]
        if title:
            parts.append(f"title={_text_excerpt(str(title), radius=text_limit // 2)}")
        if description:
            parts.append(
                f"description={_text_excerpt(str(description), radius=text_limit // 2)}"
            )
        if value:
            parts.append(f"value={_text_excerpt(str(value), radius=text_limit // 2)}")
        lines.append(f"  {'  ' * depth}- " + " ".join(parts))

        if depth >= max_depth:
            children = list(node.children)
            if children:
                lines.append(
                    f"  {'  ' * (depth + 1)}... depth limit, {len(children)} children"
                )
            return

        for child in list(node.children):
            if remaining["nodes"] <= 0:
                lines.append("  ... node limit reached")
                return
            walk(child, depth + 1)

    walk(el, 0)
    return lines


@mod.action_class
class Actions:
    def debug_accessibility(el: Element = None):
        """Prints information about the currently focused UI element to the terminal, for debugging"""

        if not el:
            el = ui.focused_element()

        try:
            # TODO(pcohen): make this work without Rich installed
            from rich.console import Console

            console = Console(color_system="truecolor", soft_wrap=True)

            console.rule(f"{str(el)}'s attributes:")

            # Attempt to sort the keys by relying on insertion order.
            attributed = {}
            for k in sorted(el.attrs):
                attributed[k] = el.get(k)

            console.print(attributed, markup=False)
        except Exception as e:
            print(f'Exception while debugging accessibility: "{e}":')
            traceback.print_exc()

    def debug_dictation_context(
        radius: int = 300, tree_depth: int = 8, max_nodes: int = 220
    ):
        """Prints detailed dictation accessibility context and offset mapping diagnostics."""

        try:
            el = actions.user.dictation_current_element()
            raw_context = _raw_context_from_element(el)
            adjusted_context = (
                actions.user.accessibility_adjust_context_for_application(
                    el, replace(raw_context)
                )
            )

            print("\n=== axkit dictation context debug ===")
            app = ui.active_app()
            window = ui.active_window()
            print(f"active_app={app}")
            print(f"active_window={window}")
            print(
                "settings: "
                f"accessibility_dictation={settings.get('user.accessibility_dictation')} "
                f"dictation_debug_mode={settings.get('user.dictation_debug_mode')}"
            )

            for line in _element_summary(el):
                print(line)

            for line in _span_text_window(
                "raw AXValue context",
                raw_context.content,
                raw_context.selection,
                radius,
            ):
                print(line)

            for line in _span_text_window(
                "adjusted dictation context",
                adjusted_context.content,
                adjusted_context.selection,
                radius,
            ):
                print(line)

            for line in _dictation_peek_lines(adjusted_context, radius):
                print(line)

            for line in chromium_text_model_debug_lines(el, raw_context):
                print(line)

            for line in _tree_lines(el, tree_depth, max_nodes):
                print(line)
            print("=== end axkit dictation context debug ===\n")
        except Exception as e:
            print(f'Exception while debugging dictation context: "{e}":')
            traceback.print_exc()


active_hiss = {"cron": None}


def hiss_over_threshold():
    if not active_hiss.get("start"):
        return False

    return time.time() - active_hiss["start"] > settings.get(
        "user.hiss_to_debug_accessibility_threshold"
    )


def stop_hiss():
    trigger = hiss_over_threshold()

    if active_hiss["cron"]:
        cron.cancel(active_hiss["cron"])
        active_hiss["cron"] = None

    active_hiss["start"] = None

    if trigger:
        actions.user.debug_accessibility()


def check_hiss():
    if hiss_over_threshold():
        stop_hiss()


def start_hiss():
    active_hiss["start"] = time.time()
    active_hiss["cron"] = cron.interval("32ms", check_hiss)


def on_hiss(noise_active: bool):
    if not settings.get("user.hiss_to_debug_accessibility"):
        return

    if noise_active:
        start_hiss()
    else:
        stop_hiss()


noise.register("hiss", on_hiss)
