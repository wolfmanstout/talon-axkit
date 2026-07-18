"""Shared text model for Chromium-based Google editors (Gmail compose, Google
Chat compose, Google Docs).

Empirically (Chrome, June 2026, macOS), all three editors expose the same
accessibility structure, and the same set of deviations from NSAccessibility
expectations:

AXValue concatenates the text of the AX tree's leaves with:
  - '\n' between two adjacent non-empty blocks (paragraph AXGroups, AXLists,
    and list items), but NO separator between adjacent inline leaves (e.g. the
    styled text runs of the first Gmail paragraph, which are hoisted to the
    top level of the text area);
  - '\n' for an empty paragraph (its <br>); boundaries adjacent to an empty
    paragraph contribute nothing;
  - list marker text ('• ', '◦ ', ...) included verbatim;
  - '\n' for an empty list item's <br>, but ONLY when the item is the last
    item of its list;
  - '\n' (block-final) or '\n\n' (mid-block) for inline images such as Google
    Chat emoji.

AXSelectedTextRange does NOT index into AXValue. It is measured in UTF-16 code
units over a different rendering of the same tree which:
  - includes text leaves and the full list marker text;
  - counts 1 unit for each empty paragraph or empty list item <br> (even when
    AXValue does not render it);
  - counts 0 units for the inter-block '\n' separators and for inline images.

Additionally, when the caret is inside an EMPTY list item, Chromium reports a
collapsed selection that is not the caret position at all: it is the position
of the containing list's start (in AXValue coordinates in Gmail, e.g. <6-6>
after 'Intro\n'; in selection coordinates in Google Docs, e.g. <5-5> after
'Intro'; <0-0> in both when the list starts the document). When that bogus
value cannot be confused with a legitimate caret position we remap it to the
empty item; otherwise we force a fallback rather than return a wrong context.

This module reconstructs AXValue from the tree (bailing out via content=None
when the reconstruction does not match, so callers fall back to the keystroke
method), then translates AXSelectedTextRange through the offset model above.
"""

from talon.types import Span

TEXT = "text"
MARKER = "marker"
SEP = "sep"
BR = "br"
IMAGE = "image"


def utf16_len(text):
    return len(text) + sum(1 for ch in text if ord(ch) > 0xFFFF)


def codepoint_index_for_utf16_index(text, units):
    count = 0
    for index, ch in enumerate(text):
        if count >= units:
            return index
        count += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


class AxTextSegment:
    """One run of content with its three renderings.

    ax_text: contribution to AXValue.
    peek_text: contribution to the text we report to the dictation formatter.
    offset_text: contribution to the AXSelectedTextRange coordinate space,
        measured in UTF-16 code units.
    ambiguous_before: a selection offset immediately before this segment can
        represent either side of the segment. The model falls back rather than
        guessing when a selection endpoint lands there.
    """

    def __init__(
        self,
        ax_text,
        peek_text=None,
        offset_text=None,
        kind=TEXT,
        ambiguous_before=False,
    ):
        self.ax_text = ax_text
        if peek_text is None:
            peek_text = ax_text
        if offset_text is None:
            offset_text = peek_text
        self.peek_text = peek_text
        self.offset_text = offset_text
        self.kind = kind
        self.ambiguous_before = ambiguous_before


class _ListInfo:
    def __init__(self, start_index):
        self.start_index = start_index
        self.empty_br_indices = []


class _BlockInfo:
    def __init__(self, el, segments):
        self.el = el
        self.segments = segments


class _EmptyListItemInfo:
    def __init__(self, el, marker, br_segment):
        self.el = el
        self.marker = marker
        self.br_segment = br_segment


def _text_segment(value):
    # Gmail substitutes U+00A0 for spaces around formatting boundaries; keep
    # AXValue verbatim but report a plain space to the dictation formatter.
    return AxTextSegment(value, peek_text=value.replace("\xa0", " "))


def _collect_inline(el, pieces):
    role = el.get("AXRole")
    if role == "AXImage":
        pieces.append((IMAGE, el.get("AXDescription") or ""))
        return
    if role == "AXListMarker":
        # Markers are handled by the list item walk; a marker encountered here
        # would be misplaced, but recording it keeps the validation honest.
        pieces.append((MARKER, el.get("AXValue") or ""))
        return
    children = list(el.children)
    if children:
        for child in children:
            _collect_inline(child, pieces)
        return
    value = el.get("AXValue")
    if value:
        pieces.append((TEXT, value))


def _inline_segments(pieces):
    segments = []
    for index, (kind, value) in enumerate(pieces):
        if kind == TEXT:
            segments.append(_text_segment(value))
        elif kind == MARKER:
            segments.append(AxTextSegment(value, kind=MARKER))
        else:
            # Inline images render as '\n' in AXValue ('\n\n' when more
            # content follows in the same block) and occupy no offset units.
            followed = any(v for _, v in pieces[index + 1 :])
            ax_text = "\n\n" if followed else "\n"
            segments.append(
                AxTextSegment(ax_text, peek_text=value, offset_text="", kind=IMAGE)
            )
    return segments


def _has_content(pieces):
    return any(kind in (TEXT, MARKER) and value for kind, value in pieces)


class _TreeModel:
    def __init__(self):
        self.segments = []
        self.lists = []
        self.blocks = []
        self.empty_list_items = []

    def _add_list_item(self, item, is_last_in_list, list_info):
        marker = None
        pieces = []
        for child in list(item.children):
            if marker is None and child.get("AXRole") == "AXListMarker":
                marker = child.get("AXValue") or ""
                continue
            _collect_inline(child, pieces)

        if marker is not None:
            self.segments.append(AxTextSegment(marker, kind=MARKER))

        if _has_content(pieces) or any(kind == IMAGE for kind, _ in pieces):
            self.segments.extend(_inline_segments(pieces))
            return

        # Empty list item: its <br> occupies one offset unit, but renders in
        # AXValue only when the item closes its list.
        br = AxTextSegment(
            "\n" if is_last_in_list else "",
            peek_text="\n" if is_last_in_list else "",
            offset_text="\n",
            kind=BR,
        )
        list_info.empty_br_indices.append(len(self.segments))
        self.segments.append(br)
        self.empty_list_items.append(_EmptyListItemInfo(item, marker or "", br))

    def _add_list(self, list_el):
        list_info = _ListInfo(len(self.segments))
        self.lists.append(list_info)
        items = list(list_el.children)
        for index, item in enumerate(items):
            if index:
                self.segments.append(AxTextSegment("\n", offset_text="", kind=SEP))
            if item.get("AXRole") == "AXList":
                self._add_list(item)
                continue
            self._add_list_item(item, index == len(items) - 1, list_info)

    def _add_block(self, child):
        """Returns (has_content, inline) for separator decisions."""
        role = child.get("AXRole")
        if role == "AXList":
            before = len(self.segments)
            self._add_list(child)
            has_content = any(
                segment.ax_text or segment.offset_text
                for segment in self.segments[before:]
                if segment.kind in (TEXT, MARKER)
            )
            return has_content, False

        if role in ("AXStaticText", "AXImage", "AXListMarker"):
            # Inline run hoisted to the top level (Gmail first paragraph).
            pieces = []
            _collect_inline(child, pieces)
            self.segments.extend(_inline_segments(pieces))
            return _has_content(pieces), True

        # Paragraph-style block (AXGroup etc).
        pieces = []
        _collect_inline(child, pieces)
        if not pieces:
            # Empty groups represent <br>s. Chromium conflates a caret before
            # the <br> with one after it, so the boundary cannot be mapped
            # reliably from AXSelectedTextRange alone.
            self.segments.append(
                AxTextSegment("\n", offset_text="\n", kind=BR, ambiguous_before=True)
            )
            return False, False
        self.segments.extend(_inline_segments(pieces))
        return _has_content(pieces), False

    def build(self, el, ax_value=None):
        previous = None  # (has_content, inline) of previous child
        for child in list(el.children):
            previous = self._add_block_with_separator(child, previous, ax_value)
        return self

    def _add_block_with_separator(self, child, previous, ax_value):
        # Decide whether a separator precedes this child: only between two
        # adjacent content-bearing children. Adjacent inline runs normally
        # concatenate, but literal line breaks inserted into Gmail are exposed
        # as separate top-level leaves with the newline present only at the
        # root. In that case the newline is part of offset space too.
        start = len(self.segments)
        has_content, inline = self._add_block(child)
        block_segments = self.segments[start:]
        structural_separator = (
            previous is not None
            and previous[0]
            and has_content
            and not (previous[1] and inline)
        )
        inline_line_break = False
        if (
            ax_value is not None
            and previous is not None
            and previous[0]
            and has_content
            and previous[1]
            and inline
        ):
            prefix = segment_text(self.segments[:start], "ax_text")
            block_text = segment_text(block_segments, "ax_text")
            inline_line_break = ax_value.startswith(prefix + "\n" + block_text)

        if structural_separator or inline_line_break:
            offset_text = "\n" if inline_line_break else ""
            self.segments.insert(
                start, AxTextSegment("\n", offset_text=offset_text, kind=SEP)
            )
            for list_info in self.lists:
                if list_info.start_index >= start:
                    list_info.start_index += 1
                    list_info.empty_br_indices = [
                        index + 1 for index in list_info.empty_br_indices
                    ]
        self.blocks.append(_BlockInfo(child, block_segments))
        return has_content, inline


def segment_text(segments, attr):
    return "".join(getattr(segment, attr) for segment in segments)


def offset_to_text_index(segments, offset):
    """Map a UTF-16 offset-space position to a codepoint index in peek text."""
    text_index = 0
    remaining = offset
    for segment in segments:
        offset_units = utf16_len(segment.offset_text)
        if offset_units:
            if remaining <= 0:
                return text_index
            if remaining < offset_units:
                if segment.offset_text == segment.peek_text:
                    return text_index + codepoint_index_for_utf16_index(
                        segment.peek_text, remaining
                    )
                return text_index + min(len(segment.peek_text), remaining)
            remaining -= offset_units
        text_index += len(segment.peek_text)
    return text_index


def _offset_units_before(segments, index):
    return sum(utf16_len(segment.offset_text) for segment in segments[:index])


def _ax_units_before(segments, index):
    return sum(utf16_len(segment.ax_text) for segment in segments[:index])


def _selection_touches_ambiguous_boundary(segments, selection):
    position = 0
    ambiguous_offsets = set()
    for segment in segments:
        if segment.ambiguous_before:
            ambiguous_offsets.add(position)
        position += utf16_len(segment.offset_text)
    return selection.left in ambiguous_offsets or selection.right in ambiguous_offsets


def _selected_block_boundary_resolution(model):
    """Resolve a conflated break boundary from descendant selection state.

    Chromium exposes a local AXSelectedTextRanges value on the top-level block
    that actually owns the caret, even when the text area's range conflates the
    two sides of a <br> or becomes otherwise bogus.
    """
    selected_blocks = []
    for block_index, block in enumerate(model.blocks):
        try:
            ranges = block.el.get("AXSelectedTextRanges")
        except Exception:
            continue
        if ranges is not None:
            selected_blocks.append((block_index, block, ranges))

    if len(selected_blocks) != 1:
        return None

    block_index, block, ranges = selected_blocks[0]
    if len(ranges) != 1 or ranges[0].left != ranges[0].right or not block.segments:
        return None

    local_selection = ranges[0]
    segment_indices = {
        id(segment): index for index, segment in enumerate(model.segments)
    }
    first_index = segment_indices[id(block.segments[0])]
    peek_start = sum(len(segment.peek_text) for segment in model.segments[:first_index])

    if all(segment.kind == BR for segment in block.segments) and any(
        segment.ambiguous_before for segment in block.segments
    ):
        # A selected empty block means the caret is on that visual line, after
        # the newline represented by its <br> in peek text.
        local_peek_index = sum(len(segment.peek_text) for segment in block.segments)
    else:
        local_offset_units = sum(
            utf16_len(segment.offset_text) for segment in block.segments
        )
        if not 0 <= local_selection.left <= local_offset_units:
            return None
        local_peek_index = offset_to_text_index(block.segments, local_selection.left)

    peek_index = peek_start + local_peek_index
    ambiguous_boundaries = set()
    segment_peek_start = 0
    for segment in model.segments:
        if segment.ambiguous_before:
            ambiguous_boundaries.add(segment_peek_start)
            ambiguous_boundaries.add(segment_peek_start + len(segment.peek_text))
        segment_peek_start += len(segment.peek_text)

    if peek_index not in ambiguous_boundaries:
        return None
    return block_index, local_selection, peek_index


def _selected_empty_list_item_resolution(model):
    """Resolve Chromium's bogus root range from the selected empty list item.

    The empty item's own AXSelectedTextRanges is local to the item and places
    the caret immediately after its marker. That ownership signal remains
    accurate when the root range points at the containing list's start.
    """
    selected_items = []
    for item_index, info in enumerate(model.empty_list_items):
        try:
            ranges = info.el.get("AXSelectedTextRanges")
        except Exception:
            continue
        if ranges is not None:
            selected_items.append((item_index, info, ranges))

    if len(selected_items) != 1:
        return None

    item_index, info, ranges = selected_items[0]
    if len(ranges) != 1 or ranges[0].left != ranges[0].right:
        return None

    local_selection = ranges[0]
    expected_offset = utf16_len(info.marker)
    if local_selection.left != expected_offset:
        return None

    try:
        br_index = model.segments.index(info.br_segment)
    except ValueError:
        return None
    peek_index = sum(len(segment.peek_text) for segment in model.segments[:br_index])
    return item_index, local_selection, peek_index


def _is_unreachable_offset(segments, value):
    """True if no legitimate caret can sit at `value` in offset space."""
    if value == 0:
        return bool(segments) and segments[0].kind == MARKER
    position = 0
    for segment in segments:
        units = utf16_len(segment.offset_text)
        if segment.kind == MARKER and position < value < position + units:
            return True
        position += units
    return False


_FALLBACK = object()


def _resolve_collapsed_selection(model, value):
    """Detect Chromium's bogus empty-list-item selections.

    Returns None to use the value as-is, a replacement offset to remap, or
    _FALLBACK when the value cannot be trusted.
    """
    segments = model.segments
    empty_lists = [info for info in model.lists if info.empty_br_indices]
    first_is_marker = bool(segments) and segments[0].kind == MARKER

    for info in empty_lists:
        bogus_candidates = {
            _offset_units_before(segments, info.start_index),
            _ax_units_before(segments, info.start_index),
        }
        if value in bogus_candidates:
            if (
                _is_unreachable_offset(segments, value)
                and len(info.empty_br_indices) == 1
            ):
                return _offset_units_before(segments, info.empty_br_indices[0])
            return _FALLBACK

    if value == 0 and empty_lists:
        # An empty item exists somewhere and 0 was not attributable above;
        # Chromium also reports <0-0> in cases we cannot disambiguate.
        return _FALLBACK
    if value == 0 and first_is_marker:
        # The minimum legitimate offset in a marker-first document is the
        # marker's length; <0-0> is always bogus here.
        return _FALLBACK
    return None


def apply_chromium_text_model(el, context):
    """Validate AXValue against the tree and translate the selection.

    On any mismatch sets context.content to None so the caller falls back.
    """
    model = _TreeModel().build(el, context.content)
    segments = model.segments

    ax_text = segment_text(segments, "ax_text")
    if ax_text != context.content:
        context.content = None
        return context

    selection = context.selection
    if selection.left == selection.right:
        block_resolution = _selected_block_boundary_resolution(model)
        if block_resolution is not None:
            _, _, peek_index = block_resolution
            context.content = segment_text(segments, "peek_text")
            context.selection = Span(peek_index, peek_index)
            return context

        empty_item_resolution = _selected_empty_list_item_resolution(model)
        if empty_item_resolution is not None:
            _, _, peek_index = empty_item_resolution
            context.content = segment_text(segments, "peek_text")
            context.selection = Span(peek_index, peek_index)
            return context

    if selection.left == selection.right:
        resolution = _resolve_collapsed_selection(model, selection.left)
        if resolution is _FALLBACK:
            context.content = None
            return context
        if resolution is not None:
            selection = Span(resolution, resolution)

    if _selection_touches_ambiguous_boundary(segments, selection):
        context.content = None
        return context

    context.content = segment_text(segments, "peek_text")
    context.selection = Span(
        offset_to_text_index(segments, selection.left),
        offset_to_text_index(segments, selection.right),
    )
    return context


def _repr_text(text, limit=80):
    if text is None:
        return "None"
    value = repr(text)
    if len(value) <= limit:
        return value
    return value[: limit - 4] + "...'"


def chromium_text_model_debug_lines(el, context=None, max_segments=160):
    """Return human-readable diagnostics for the Chromium AX text model."""
    model = _TreeModel().build(el, context.content if context is not None else None)
    segments = model.segments
    ax_text = segment_text(segments, "ax_text")
    peek_text = segment_text(segments, "peek_text")
    offset_text = segment_text(segments, "offset_text")

    lines = [
        "Chromium text model:",
        f"  segments={len(segments)} lists={len(model.lists)}",
        f"  ax_text_len={len(ax_text)} ax_text_utf16={utf16_len(ax_text)}",
        f"  peek_text_len={len(peek_text)} peek_text_utf16={utf16_len(peek_text)}",
        f"  offset_text_len={len(offset_text)} offset_text_utf16={utf16_len(offset_text)}",
    ]

    if context is not None:
        lines.append(f"  validates_ax_value={ax_text == context.content}")
        selection = context.selection
        if ax_text != context.content:
            lines.append("  selection_resolution=SKIPPED_AX_VALUE_MISMATCH")
        elif selection is None:
            lines.append("  selection=None")
        else:
            lines.append(f"  raw_selection={selection}")
            selection_falls_back = False
            block_resolution = None
            empty_item_resolution = None
            if selection.left == selection.right:
                block_resolution = _selected_block_boundary_resolution(model)
                if block_resolution is not None:
                    block_index, local_selection, peek_index = block_resolution
                    lines.append(
                        "  selected_block_boundary_resolution="
                        f"block_{block_index}_local_{local_selection}_to_peek_{peek_index}"
                    )
                    selection = Span(peek_index, peek_index)
                else:
                    empty_item_resolution = _selected_empty_list_item_resolution(model)
                    if empty_item_resolution is not None:
                        item_index, local_selection, peek_index = empty_item_resolution
                        lines.append(
                            "  selected_empty_list_item_resolution="
                            f"item_{item_index}_local_{local_selection}_to_peek_{peek_index}"
                        )
                        selection = Span(peek_index, peek_index)
                    else:
                        resolution = _resolve_collapsed_selection(model, selection.left)
                        if resolution is _FALLBACK:
                            lines.append("  collapsed_selection_resolution=FALLBACK")
                            selection_falls_back = True
                        elif resolution is None:
                            lines.append("  collapsed_selection_resolution=unchanged")
                        else:
                            lines.append(
                                f"  collapsed_selection_resolution=remap_to_{resolution}"
                            )
                            selection = Span(resolution, resolution)
            if block_resolution is None and empty_item_resolution is None:
                if _selection_touches_ambiguous_boundary(segments, selection):
                    lines.append("  selection_resolution=FALLBACK_AMBIGUOUS_BOUNDARY")
                    selection_falls_back = True
            if not selection_falls_back:
                if block_resolution is not None or empty_item_resolution is not None:
                    lines.append(f"  mapped_selection={selection}")
                else:
                    lines.append(
                        "  mapped_selection="
                        f"<{offset_to_text_index(segments, selection.left)}-"
                        f"{offset_to_text_index(segments, selection.right)}>"
                    )

    lines.append("  segment table:")
    ax_units = 0
    peek_index = 0
    offset_units = 0
    for index, segment in enumerate(segments[:max_segments]):
        segment_ax_units = utf16_len(segment.ax_text)
        segment_offset_units = utf16_len(segment.offset_text)
        lines.append(
            "    "
            f"{index:03d} kind={segment.kind} "
            f"ambiguous_before={segment.ambiguous_before} "
            f"ax[{ax_units}:{ax_units + segment_ax_units}] "
            f"peek[{peek_index}:{peek_index + len(segment.peek_text)}] "
            f"offset[{offset_units}:{offset_units + segment_offset_units}] "
            f"ax={_repr_text(segment.ax_text)} "
            f"peek={_repr_text(segment.peek_text)} "
            f"offset={_repr_text(segment.offset_text)}"
        )
        ax_units += segment_ax_units
        peek_index += len(segment.peek_text)
        offset_units += segment_offset_units

    if len(segments) > max_segments:
        lines.append(f"    ... {len(segments) - max_segments} more segments omitted")

    return lines
