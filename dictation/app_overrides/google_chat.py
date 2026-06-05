from talon import Context, Module

from ..ax_tree_text import AxTextSegment, apply_tree_text_model

ctx = Context()
ctx.matches = r"""
os: mac
app: chrome
title: / - Chat/
"""
mod = Module()


def has_list_marker(el):
    elements = list(el.children)
    while elements:
        first = elements.pop(0)
        if first.get("AXRole") == "AXListMarker":
            return True

        elements[0:0] = list(first.children)

    return False


def google_chat_leaf_segment(el):
    if el.get("AXRole") == "AXListMarker":
        return AxTextSegment(el.get("AXValue") or "", peek_text="", offset_text="")
    return AxTextSegment(el.get("AXValue") or "")


@ctx.action_class("user")
class Actions:
    def accessibility_adjust_context_for_application(el, context):
        if context.content is None:
            return context

        # Google Chat represents emoji in the compose box as "\n\n" in AXValue,
        # but AXSelectedTextRange offsets skip those placeholders. Real blank
        # lines can also produce "\n\n", so fall back instead of guessing.
        if "\n\n" in context.content:
            context.content = None
            return context

        # Google Chat includes AXListMarker text in AXValue, but its
        # AXSelectedTextRange offsets are measured as though list markers were
        # absent. Reconstruct the text from the tree so we only normalize when
        # the tree accounts for the whole AXValue.
        if has_list_marker(el):
            return apply_tree_text_model(
                el, context, leaf_segment=google_chat_leaf_segment
            )

        return context
