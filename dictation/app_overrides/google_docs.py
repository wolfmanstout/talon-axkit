from talon import Context, Module

ctx = Context()
ctx.matches = r"""
os: mac
app: google_docs
"""
mod = Module()


def text_from_tree(el, normalize_list_markers=False):
    role = el.get("AXRole")
    if role == "AXListMarker":
        marker = el.get("AXValue") or ""
        return marker.removesuffix(" ") if normalize_list_markers else marker

    child_text = [
        text_from_tree(child, normalize_list_markers) for child in list(el.children)
    ]
    if role == "AXList":
        return "\n".join(child_text)
    if child_text:
        return "".join(child_text)

    return el.get("AXValue") or ""


def text_area_content_from_tree(el, normalize_list_markers=False):
    return "\n".join(
        text_from_tree(child, normalize_list_markers) for child in list(el.children)
    )


@ctx.action_class("user")
class Actions:
    def accessibility_adjust_context_for_application(el, context):
        if context.content is None:
            return context

        # Docs includes AXListMarker separator spaces in AXValue, but omits
        # them from AXSelectedTextRange offsets. Only normalize content when
        # the accessibility tree accounts for the whole AXValue.
        raw_tree_content = text_area_content_from_tree(el)
        if raw_tree_content == context.content:
            context.content = text_area_content_from_tree(
                el, normalize_list_markers=True
            )

        return context
