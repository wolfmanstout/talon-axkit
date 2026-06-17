from talon import Context, Module

from ..ax_tree_text import apply_chromium_text_model

ctx = Context()
# The compose box appears both in conversation views ("<name> - Chat") and in
# the Home view, whose title is just "Chat". apply_chromium_text_model
# validates the tree against AXValue, so a false title match degrades to the
# keystroke fallback rather than producing a wrong context.
ctx.matches = r"""
os: mac
app: google chat
"""
mod = Module()


@ctx.action_class("user")
class Actions:
    def accessibility_adjust_context_for_application(el, context):
        if context.content is None:
            return context

        # Google Chat shares the Chromium editor model with Gmail, including
        # picker emoji exposed as AXImage children that render as '\n'/'\n\n'
        # in AXValue but occupy no AXSelectedTextRange offsets.
        return apply_chromium_text_model(el, context)
