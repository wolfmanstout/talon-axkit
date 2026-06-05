from talon import Context, Module
from talon.types import Span

ctx = Context()
ctx.matches = r"""
os: mac
app: gmail
"""
mod = Module()


def text_from_tree(el):
    role = el.get("AXRole")
    if role == "AXListMarker":
        return el.get("AXValue") or ""

    child_text = [text_from_tree(child) for child in list(el.children)]
    if role == "AXList":
        return "\n".join(child_text)
    if child_text:
        return "".join(child_text)

    return el.get("AXValue") or ""


def text_area_content_from_tree(el):
    return "\n".join(
        child_text
        for child_text in (text_from_tree(child) for child in list(el.children))
        if child_text
    )


def text_segments_from_tree(el):
    role = el.get("AXRole")
    if role == "AXList":
        segments = []
        for index, child in enumerate(list(el.children)):
            if index:
                segments.append(("\n", ""))
            segments.extend(text_segments_from_tree(child))
        return segments

    child_segments = []
    for child in list(el.children):
        child_segments.extend(text_segments_from_tree(child))
    if child_segments:
        return child_segments

    text = el.get("AXValue") or ""
    if text:
        return [(text, text)]

    return []


def text_area_segments_from_tree(el):
    segments = []
    saw_content = False
    previous_separator_was_explicit = False

    for child in list(el.children):
        child_segments = text_segments_from_tree(child)
        child_content = "".join(raw for raw, _ in child_segments)
        is_empty_group = child.get("AXRole") == "AXGroup" and not child_content

        if is_empty_group:
            if saw_content:
                segments.append(("\n", "\n"))
                previous_separator_was_explicit = True
            continue

        if not child_content:
            continue

        if saw_content and not previous_separator_was_explicit:
            segments.append(("\n", ""))

        segments.extend(child_segments)
        saw_content = True
        previous_separator_was_explicit = False

    return segments


def offset_to_raw_index(segments, offset):
    raw_index = 0
    remaining = offset

    for raw, offset_text in segments:
        offset_length = len(offset_text)
        if offset_length:
            if remaining < offset_length:
                return raw_index + remaining
            remaining -= offset_length

        raw_index += len(raw)

    return raw_index


@ctx.action_class("user")
class Actions:
    def accessibility_adjust_context_for_application(el, context):
        if context.content is None:
            return context

        # Gmail includes structural separators in AXValue that are absent from
        # AXSelectedTextRange offsets. Reconstruct the raw text from the tree
        # first, then map Gmail's offset coordinate back into that raw text.
        raw_tree_content = text_area_content_from_tree(el)
        if raw_tree_content != context.content:
            context.content = None
            return context

        segments = text_area_segments_from_tree(el)
        context.selection = Span(
            offset_to_raw_index(segments, context.selection.left),
            offset_to_raw_index(segments, context.selection.right),
        )

        return context
