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
    bias_after: when the caret lands exactly before this segment, report the
        position after its peek_text instead (used for empty-paragraph line
        breaks, so a caret on a blank line sees the preceding newline).
    """

    def __init__(
        self, ax_text, peek_text=None, offset_text=None, kind=TEXT, bias_after=False
    ):
        self.ax_text = ax_text
        if peek_text is None:
            peek_text = ax_text
        if offset_text is None:
            offset_text = peek_text
        self.peek_text = peek_text
        self.offset_text = offset_text
        self.kind = kind
        self.bias_after = bias_after


class _ListInfo:
    def __init__(self, start_index):
        self.start_index = start_index
        self.empty_br_indices = []


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
            # Empty paragraph: one offset unit for its <br>, rendered '\n'.
            self.segments.append(
                AxTextSegment("\n", offset_text="\n", kind=BR, bias_after=True)
            )
            return False, False
        self.segments.extend(_inline_segments(pieces))
        return _has_content(pieces), False

    def build(self, el):
        previous = None  # (has_content, inline) of previous child
        for child in list(el.children):
            previous = self._add_block_with_separator(child, previous)
        return self

    def _add_block_with_separator(self, child, previous):
        # Decide whether a separator precedes this child: only between two
        # adjacent content-bearing children, and never between two inline runs.
        start = len(self.segments)
        has_content, inline = self._add_block(child)
        if (
            previous is not None
            and previous[0]
            and has_content
            and not (previous[1] and inline)
        ):
            self.segments.insert(start, AxTextSegment("\n", offset_text="", kind=SEP))
            for list_info in self.lists:
                if list_info.start_index >= start:
                    list_info.start_index += 1
                    list_info.empty_br_indices = [
                        index + 1 for index in list_info.empty_br_indices
                    ]
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
                if segment.bias_after:
                    return text_index + len(segment.peek_text)
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
    model = _TreeModel().build(el)
    segments = model.segments

    ax_text = segment_text(segments, "ax_text")
    if ax_text != context.content:
        context.content = None
        return context

    selection = context.selection
    if selection.left == selection.right:
        resolution = _resolve_collapsed_selection(model, selection.left)
        if resolution is _FALLBACK:
            context.content = None
            return context
        if resolution is not None:
            selection = Span(resolution, resolution)

    context.content = segment_text(segments, "peek_text")
    context.selection = Span(
        offset_to_text_index(segments, selection.left),
        offset_to_text_index(segments, selection.right),
    )
    return context
