from talon.types import Span


class AxTextSegment:
    def __init__(self, ax_text, peek_text=None, offset_text=None):
        self.ax_text = ax_text
        if peek_text is None:
            peek_text = ax_text
        if offset_text is None:
            offset_text = peek_text
        self.peek_text = peek_text
        self.offset_text = offset_text


def default_leaf_segment(el):
    return AxTextSegment(el.get("AXValue") or "")


def default_child_separator(parent, previous_child, child, index):
    if parent.get("AXRole") == "AXList" and index:
        return AxTextSegment("\n")
    return None


def default_top_level_separator(previous_child, child):
    return AxTextSegment("\n")


def segments_from_tree(
    el, leaf_segment=default_leaf_segment, child_separator=default_child_separator
):
    children = list(el.children)
    if children:
        segments = []
        previous_child = None
        for index, child in enumerate(children):
            separator = child_separator(el, previous_child, child, index)
            if separator is not None:
                segments.append(separator)
            segments.extend(segments_from_tree(child, leaf_segment, child_separator))
            previous_child = child
        return segments

    segment = leaf_segment(el)
    if segment is None:
        return []
    return [segment]


def text_area_segments_from_tree(
    el,
    leaf_segment=default_leaf_segment,
    child_separator=default_child_separator,
    top_level_separator=default_top_level_separator,
):
    segments = []
    saw_content = False
    previous_child = None

    for child in list(el.children):
        child_segments = segments_from_tree(child, leaf_segment, child_separator)
        child_ax_text = "".join(segment.ax_text for segment in child_segments)
        if not child_ax_text:
            continue

        if saw_content:
            separator = top_level_separator(previous_child, child)
            if separator is not None:
                segments.append(separator)

        segments.extend(child_segments)
        saw_content = True
        previous_child = child

    return segments


def segment_text(segments, attr):
    return "".join(getattr(segment, attr) for segment in segments)


def offset_to_text_index(segments, offset):
    text_index = 0
    remaining = offset

    for segment in segments:
        offset_length = len(segment.offset_text)
        if offset_length:
            if remaining < offset_length:
                return text_index + min(remaining, len(segment.peek_text))
            remaining -= offset_length

        text_index += len(segment.peek_text)

    return text_index


def apply_tree_text_model(
    el,
    context,
    leaf_segment=default_leaf_segment,
    child_separator=default_child_separator,
    top_level_separator=default_top_level_separator,
):
    segments = text_area_segments_from_tree(
        el, leaf_segment, child_separator, top_level_separator
    )
    ax_text = segment_text(segments, "ax_text")
    if ax_text != context.content:
        context.content = None
        return context

    context.content = segment_text(segments, "peek_text")
    context.selection = Span(
        offset_to_text_index(segments, context.selection.left),
        offset_to_text_index(segments, context.selection.right),
    )
    return context
