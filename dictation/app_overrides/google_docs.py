from talon import Context, Module

ctx = Context()
ctx.matches = r"""
os: mac
app: google_docs
"""
mod = Module()


def text_from_tree(
    el,
    normalize_list_markers=False,
    preserve_first_list_marker_separator=False,
    list_marker_count=None,
):
    if list_marker_count is None:
        list_marker_count = [0]

    role = el.get("AXRole")
    if role == "AXListMarker":
        marker = el.get("AXValue") or ""
        preserve_separator = (
            preserve_first_list_marker_separator and list_marker_count[0] == 0
        )
        list_marker_count[0] += 1
        if normalize_list_markers and not preserve_separator:
            return marker.removesuffix(" ")
        return marker

    child_text = [
        text_from_tree(
            child,
            normalize_list_markers,
            preserve_first_list_marker_separator,
            list_marker_count,
        )
        for child in list(el.children)
    ]
    if role == "AXList":
        return "\n".join(child_text)
    if child_text:
        return "".join(child_text)

    return el.get("AXValue") or ""


def text_area_content_from_tree(
    el, normalize_list_markers=False, preserve_first_list_marker_separator=False
):
    list_marker_count = [0]
    return "\n".join(
        text_from_tree(
            child,
            normalize_list_markers,
            preserve_first_list_marker_separator,
            list_marker_count,
        )
        for child in list(el.children)
    )


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

        # Docs includes AXListMarker separator spaces in AXValue, but omits
        # them from AXSelectedTextRange offsets, except for a marker starting
        # the exposed AXValue. Only normalize content when the accessibility
        # tree accounts for the whole AXValue.
        raw_tree_content = text_area_content_from_tree(el)
        if raw_tree_content != context.content:
            context.content = None
            return context

        context.content = text_area_content_from_tree(
            el,
            normalize_list_markers=True,
            preserve_first_list_marker_separator=content_starts_with_list_marker(el),
        )

        return context
