from talon import Context, Module, app, ui

from ..ax_tree_text import apply_chromium_text_model

ctx = Context()
# The compose box appears both in conversation views ("<name> - Chat") and in
# the Home view, whose title is just "Chat". apply_chromium_text_model
# validates the tree against AXValue, so a false title match degrades to the
# keystroke fallback rather than producing a wrong context.
ctx.matches = r"""
os: mac
app: chrome
app: codex
"""
mod = Module()


@ctx.action_class("user")
class Actions:
    def accessibility_adjust_context_for_application(el, context):
        if context.content is None:
            print("Context provides no content, falling back.")
            return context

        result = apply_chromium_text_model(el, context)
        if result.content is None or result.selection is None:
            print("Unable to adjust chrome context, falling back.")
        return result


def enable_manual_accessibility(active_app: ui.App):
    try:
        # This is sufficient to enable accessibility in Chromium apps, including
        # Electron apps.
        _ = active_app.element.AXRole
    except Exception:
        pass


def on_ready():
    # Enable in all apps. This operation is harmless on non-Chromium apps.
    ui.register("app_activate", enable_manual_accessibility)
    enable_manual_accessibility(ui.active_app())


if app.platform == "mac":
    app.register("ready", on_ready)
