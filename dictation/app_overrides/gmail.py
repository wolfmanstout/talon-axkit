from talon import Context, Module

from ..ax_tree_text import AxTextSegment, apply_tree_text_model

ctx = Context()
ctx.matches = r"""
os: mac
app: gmail
"""
mod = Module()


def gmail_child_separator(parent, previous_child, child, index):
    if parent.get("AXRole") == "AXList" and index:
        return AxTextSegment("\n", offset_text="")
    return None


def gmail_top_level_separator(previous_child, child):
    return AxTextSegment("\n", offset_text="")


@ctx.action_class("user")
class Actions:
    def accessibility_adjust_context_for_application(el, context):
        if context.content is None:
            return context

        # Gmail exposes structural separators in AXValue that are absent from
        # AXSelectedTextRange offsets. Keep the visible text, but translate the
        # selection through Gmail's offset coordinate.
        return apply_tree_text_model(
            el,
            context,
            child_separator=gmail_child_separator,
            top_level_separator=gmail_top_level_separator,
        )
