---
name: reviewdoc
description: Turn a plan, spec, direction, strategy, or roadmap written as markdown into a beautiful, self-contained, mobile-friendly HTML review document (NOT raw markdown) that opens in the browser and captures per-section Approve/Rework feedback. Use whenever you are about to hand someone a written plan/spec/direction to read and react to. Triggers include "review this plan", "make a review doc", "render this for review", "for sign-off", "send it for review", "look this over". Does NOT apply to rendered-artifact reviews (a live UI/page, a PDF, a code diff) or a trivial one-line answer. This is for a TEXT plan/spec/direction writeup.
---

# reviewdoc — HTML review documents

Instead of handing someone raw markdown to review, render it through this skill: one self-contained HTML file (designed, mobile-friendly, works offline) that **opens in a new browser window** and lets the reviewer mark each section **Approve / Rework** and copy structured feedback back to you.

## When to use

- Any "here is the plan, review it" / "for your review" / "look this over" / "for sign-off" moment for a written document.
- Implementation plans, phase plans, strategy memos, architecture directions, option comparisons, roadmaps.

Skip it for: a rendered artifact (a live UI/page, a PDF, a code diff) or a short inline answer. This is for a TEXT writeup.

## How to produce one

1. Write the plan as markdown to a file.
2. Run the generator (it opens the result in a NEW browser window):
   ```bash
   python3 build.py <plan>.md
   ```
   - Output: `<plan>.html` next to the source.
   - `-o <path>` choose output location · `--no-open` suppress the browser · `$REVIEWDOC_BROWSER` pick a browser.
   - Python >= 3.10, one dependency (`markdown-it-py`).
3. Tell the user it is open. They mark sections, hit **Copy feedback**, paste the block back; you map it 1:1 to sections and revise within that exact scope.

## Authoring conventions

- Optional frontmatter: `title`, `subtitle`, `project`, `date`, `status`.
- `##` = a review SECTION (gets a card + Approve/Rework controls). `###` = a subsection inside the card.
- Heading chips: `## Build {priority=high status=now}` (priority: high/medium/low).
- Callouts: `> [!decision]` / `[!risk]` / `[!question]` / `[!note]`.
- Keep paragraphs and bullets short (aim for <= 4 lines each) so they read on a phone.
- Leading emoji on headings are stripped automatically (the design owns emphasis).

## The feedback loop

Per section: `[Approve] [Rework]` + an optional note, persisted per-device in `localStorage`. **Copy feedback** emits a section-keyed text block, for example:

```
Review: <title> · <reviewer>
§2 Build   rework — "scope too broad, split into 2a/2b"
§4 QA      approve
```

The reviewer pastes that back; you revise the exact sections named.

## Theme

The entire look lives in the `TEMPLATE` string inside `build.py` (cyan/magenta editorial, Instrument Sans + Inter, dark default + light toggle, 8pt grid, type scale {32,24,16,14,12}). Edit it once and every future doc inherits it. Swap the color vars near the top of `TEMPLATE` to rebrand.
