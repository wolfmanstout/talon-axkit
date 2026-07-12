# Accessibility quirks of Google web editors (Gmail compose, Google Chat, Google Docs)

Empirical findings from a controlled investigation on macOS (Chrome, June 2026),
driving each editor inside a talonbox VM and recording `AXValue`,
`AXSelectedTextRange`, `AXNumberOfCharacters`, `AXStringForRange`, and the AX
subtree at every caret position (arrow-key "caret walks"). These findings are
the basis for `dictation/ax_tree_text.py`.

All three editors are Chromium contenteditable surfaces and exhibit the _same_
family of deviations; per-app sections below note the differences. Section
references like "GA", "C5", "D12" refer to the captured scenarios in the
investigation transcript.

## The core violation

For an `AXTextArea`, macOS accessibility clients expect `AXSelectedTextRange`,
`AXNumberOfCharacters`, `AXStringForRange`, and `AXVisibleCharacterRange` to
index into the string returned by `AXValue`. In these editors they do not:

- `AXValue`, `AXNumberOfCharacters`, and `AXStringForRange` agree with each
  other (one coordinate system, "value space").
- `AXSelectedTextRange` is measured in a _different_ coordinate system
  ("offset space"), in UTF-16 code units, over a different rendering of the
  document.

### Value space (AXValue)

`AXValue` concatenates the AX tree's leaf text with:

| Construct                                                                                                           | Contribution                                                                    |
| ------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Text leaf (`AXStaticText`)                                                                                          | its text (with U+00A0 substituted around inline-formatting boundaries in Gmail) |
| Boundary between two adjacent non-empty blocks                                                                      | `\n`                                                                            |
| Boundary between adjacent top-level _inline_ runs (Gmail hoists the first paragraph's styled runs to the top level) | nothing                                                                         |
| Empty paragraph (`<div><br></div>` → empty `AXGroup`)                                                               | `\n`; boundaries adjacent to it contribute nothing                              |
| List marker (`AXListMarker`)                                                                                        | its text verbatim (`'• '`, `'◦ '`, `'1. '`)                                     |
| Empty list item's `<br>`                                                                                            | `\n` only when the item is the **last** item of its list                        |
| Inline image (Chat picker emoji, `AXImage`)                                                                         | `\n` when block-final, `\n\n` mid-block                                         |

Notable consequence: a blank line between two paragraphs is _invisible_ in
`AXValue` beyond a single `\n` ('Para one\nPara three' for three visual lines),
and `'X'` + two blank lines + `'Y'` renders as `'X\n\nY'` (the two `\n` are the
`<br>`s, not the three boundaries).

### Offset space (AXSelectedTextRange)

| Construct                                | Units                                                                  |
| ---------------------------------------- | ---------------------------------------------------------------------- |
| Text                                     | UTF-16 code units (non-BMP chars such as emoji count 2)                |
| List marker                              | full marker text (2 for `'• '`) — caret cannot sit inside or before it |
| Empty paragraph / empty list item `<br>` | 1 (for empty list items, even when `AXValue` renders nothing)          |
| Inter-block `\n` separators              | 0                                                                      |
| Inline images                            | 0                                                                      |

Consequences:

- End-of-paragraph N and start-of-paragraph N+1 share an offset (conflated).
- Gmail can also expose a separating `<br>` that occupies one offset unit,
  while reporting the same boundary offset for a caret immediately before or
  after it. These boundary positions are likewise ambiguous.
- Selection offsets drift from `AXValue` indices by one per block boundary;
  a caret at end-of-document reports e.g. `<22-22>` for a 23-char `AXValue`
  (two paragraphs) or `<369-369>` for a 409-char one (41 paragraphs).
- Every caret position after an emoji is +1 in offset space relative to
  Python/codepoint indexing.

## The empty-list-item selection bug

When the caret is inside a list item whose only AX content is its marker
(i.e. the user just pressed Enter to create the next bullet — exactly when a
dictation user wants context), `AXSelectedTextRange` does not describe the
caret at all. It reports a collapsed range at **the position where the
containing `AXList` starts**:

- Gmail measures this bogus value in _value space_ (e.g. `<6-6>` after
  `'Intro\n'`; `<0-0>` when the list starts the document).
- Google Docs measures it in _offset space_ (e.g. `<5-5>` = end of `'Intro'`;
  `<0-0>` when list-first). This collides with the _legitimate_ caret position
  at the end of the preceding paragraph, making the two indistinguishable —
  previously misread as a "stale" offset.
- Google Chat behaves like Gmail/Docs with `<0-0>` for list-first documents
  (its compose box is almost always list-first when lists are used).

The bogus value is persistent (settles ~40 ms after the keystroke and stays),
not a transient staleness.

In marker-first documents the minimum legitimate offset is the first marker's
length (the caret cannot precede the marker), so `<0-0>` is never legitimate
there — useful both for detection and for a bug report.

## Other observations

- `AXInsertionPointLineNumber` is unreliable in all three (usually 0 or wrong).
- `AXStringForRange` intermittently fails (attribute fetch error) right after
  edits, and **requesting a range that ends inside a surrogate pair crashed
  the AX host process** (observed killing Talon) — value space is codepoint-
  indexed while `AXNumberOfCharacters` is UTF-16, so a client computing ranges
  from `len(AXValue)` can hit this.
- AX state (value + selection together) settles ~30-40 ms after a keystroke;
  reads before that return the previous, self-consistent state.
- Google Docs exposes an empty document as `AXValue='\xa0'` with selection
  `<0-1>`.
- Google Chat window titles: conversation views are "<name> - Chat" but the
  Home view (with its side-panel compose box) is just "Chat", which a
  `/ - Chat/` matcher misses.

## Suggested bug reports (for Google / Chromium)

1. `AXSelectedTextRange` is not in `AXValue` coordinates (block separators and
   inline images excluded; markers and `<br>`s included). Either include the
   separators in offsets or exclude them from `AXValue`. (Likely Chromium's
   `AXPlatformNodeTextField` value vs. selection serialization for rich
   contenteditables; affects any AX client doing `AXValue[sel.start]`.)
2. Caret in an empty list item reports the list's start position instead of
   the caret position (Chromium failing to anchor the selection inside an
   `<li>` whose only rendered child is the marker + `<br>`, then falling back
   to the list element's own offset).
3. `AXStringForRange` with a boundary inside a surrogate pair crashes/errors;
   `AXNumberOfCharacters` (UTF-16) and `len(AXValue)` (codepoints) disagree,
   inviting exactly that call.
4. `AXInsertionPointLineNumber` returns garbage.

## How `ax_tree_text.py` compensates

It rebuilds both renderings from the AX tree, validates the value-space
rendering against `AXValue` exactly (any mismatch → fall back to the keystroke
method), then translates `AXSelectedTextRange` through the offset-space
rendering, in UTF-16 units. Bogus empty-list-item selections are remapped to
the empty item when the reported value is provably unreachable (inside a
marker, or 0 in a marker-first document) and there is exactly one candidate
empty item; otherwise they force a fallback. Selection endpoints at conflated
`<br>` boundaries also force a fallback because the AX data cannot identify
which visual side contains the caret.
