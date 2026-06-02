from talon import Context, Module

ctx = Context()
ctx.matches = r"""
os: mac
app: chrome
title: / - Chat/
"""
mod = Module()


def text_from_tree(el, omit_list_markers=False):
    role = el.get("AXRole")
    if role == "AXListMarker":
        if omit_list_markers:
            return ""
        return el.get("AXValue") or ""

    child_text = [
        text_from_tree(child, omit_list_markers) for child in list(el.children)
    ]
    if role == "AXList":
        return "\n".join(child_text)
    if child_text:
        return "".join(child_text)

    return el.get("AXValue") or ""


def text_area_content_from_tree(el, omit_list_markers=False):
    return "\n".join(
        text_from_tree(child, omit_list_markers) for child in list(el.children)
    )


def has_list_marker(el):
    elements = list(el.children)
    while elements:
        first = elements.pop(0)
        if first.get("AXRole") == "AXListMarker":
            return True

        elements[0:0] = list(first.children)

    return False


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
            raw_tree_content = text_area_content_from_tree(el)
            if raw_tree_content != context.content:
                context.content = None
                return context

            context.content = text_area_content_from_tree(el, omit_list_markers=True)

        return context
