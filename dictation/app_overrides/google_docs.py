from talon import Context, Module

from ..ax_tree_text import AxTextSegment, apply_tree_text_model

ctx = Context()
ctx.matches = r"""
os: mac
app: google_docs
"""
mod = Module()


def content_starts_with_list_marker(el):
    elements = list(el.children)
    while elements:
        first = elements.pop(0)
        if first.get("AXRole") == "AXListMarker":
            return True

        children = list(first.children)
        if children:
            elements[0:0] = children
        elif first.get("AXValue"):
            return False

    return False


def list_markers_from_tree(el):
    markers = set()
    elements = list(el.children)
    while elements:
        first = elements.pop(0)
        if first.get("AXRole") == "AXListMarker":
            marker = first.get("AXValue")
            if marker:
                markers.add(marker)

        elements[0:0] = list(first.children)

    return markers


def content_after_selection_has_empty_list_item(el, context):
    content_after_selection = context.content[context.selection.left :]
    return any(
        f"\n{marker}\n" in content_after_selection
        or content_after_selection.startswith(f"{marker}\n")
        for marker in list_markers_from_tree(el)
    )


def google_docs_leaf_segment(preserve_first_list_marker_separator=False):
    list_marker_count = [0]

    def leaf_segment(el):
        if el.get("AXRole") == "AXListMarker":
            marker = el.get("AXValue") or ""
            preserve_separator = (
                preserve_first_list_marker_separator and list_marker_count[0] == 0
            )
            list_marker_count[0] += 1
            peek_text = marker if preserve_separator else marker.removesuffix(" ")
            return AxTextSegment(marker, peek_text=peek_text, offset_text=peek_text)

        return AxTextSegment(el.get("AXValue") or "")

    return leaf_segment


@ctx.action_class("user")
class Actions:
    def accessibility_adjust_context_for_application(el, context):
        if context.content is None:
            return context

        # Docs reports <0-0> after clicking into some empty list items, even
        # when the caret is far from the start of the exposed content. Since
        # this is indistinguishable from a real start-of-content caret, fall
        # back to the cursor-based context lookup for either case.
        if context.selection.left == 0 and context.selection.right == 0:
            context.content = None
            return context

        # At the start of a new empty list item, Docs can report a stale
        # selection offset from earlier text. The empty marker line is still in
        # AXValue, so fall back rather than using the stale offset.
        if content_after_selection_has_empty_list_item(el, context):
            context.content = None
            return context

        # Docs includes AXListMarker separator spaces in AXValue, but omits
        # them from AXSelectedTextRange offsets, except for a marker starting
        # the exposed AXValue. The shared mapper validates that the tree
        # accounts for AXValue before translating the selection.
        return apply_tree_text_model(
            el,
            context,
            leaf_segment=google_docs_leaf_segment(content_starts_with_list_marker(el)),
        )
