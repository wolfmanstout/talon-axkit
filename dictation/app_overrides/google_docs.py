from talon import Context, Module

from ..ax_tree_text import apply_chromium_text_model

ctx = Context()
ctx.matches = r"""
os: mac
app: google_docs
"""
mod = Module()


@ctx.action_class("user")
class Actions:
    def accessibility_adjust_context_for_application(el, context):
        if context.content is None:
            return context

        # Docs shares the Chromium editor model with Gmail/Chat. Its bogus
        # empty-list-item selections collide with the legitimate caret
        # position at the end of the paragraph preceding the list, so those
        # cases resolve to a fallback inside apply_chromium_text_model.
        return apply_chromium_text_model(el, context)
