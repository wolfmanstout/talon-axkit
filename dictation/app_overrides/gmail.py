from talon import Context, Module

from ..ax_tree_text import apply_chromium_text_model

ctx = Context()
ctx.matches = r"""
os: mac
app: gmail
"""
mod = Module()


@ctx.action_class("user")
class Actions:
    def accessibility_adjust_context_for_application(el, context):
        if context.content is None:
            return context

        # Gmail exposes structural separators in AXValue that are absent from
        # AXSelectedTextRange offsets (see ax_tree_text for the full model).
        return apply_chromium_text_model(el, context)
