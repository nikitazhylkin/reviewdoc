#!/usr/bin/env python3
"""
reviewdoc — turn a plan/direction markdown file into one self-contained,
beautiful, mobile-friendly HTML review doc with per-section feedback.

Usage:
    python build.py <input.md> [-o output.html] [--no-open]

By default the generated HTML opens in a NEW browser window. Pass --no-open to
suppress (tests/CI). Override the browser with the $REVIEWDOC_BROWSER env var.

Authoring conventions (all optional except `##` sectioning):
    ---                          # optional frontmatter
    title: ...
    subtitle: ...
    project: ...
    date: 2026-06-19
    status: For review
    ---
    Preamble paragraph(s) -> rendered as the cover lead.
    ## Section name {priority=high status=now}   -> a review section + chips
    ### Subsection                                -> inside the card
    > [!decision] text   (or [!risk] [!question] [!note])  -> labeled callout
Everything else is standard markdown (tables, nested lists, code, bold).
Rendering: markdown-it-py (CommonMark + tables) so 2-space nested lists nest.
"""
import sys
import os
import re
import base64
import hashlib
import html
import datetime
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Optional vendored deps (assets/vendor) keep it zero-setup if present; otherwise
# a normal `pip install markdown-it-py` is used.
_VENDOR = HERE / "assets" / "vendor"
if _VENDOR.exists():
    sys.path.insert(0, str(_VENDOR))
try:
    from markdown_it import MarkdownIt
except ModuleNotFoundError:
    sys.exit("reviewdoc needs markdown-it-py (Python >= 3.10). "
             "Install it: python3 -m pip install -r requirements.txt")

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _font_dir() -> Path:
    """Instrument Sans + Inter, bundled next to this script (assets/fonts).
    They embed as base64 so the output HTML is fully offline / self-contained."""
    return HERE / "assets" / "fonts"

_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable(["table"])

# Leading decorative emoji / symbols to strip off headings (design owns emphasis).
_LEAD_EMOJI = re.compile(
    r"^[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    r"\U0001F1E6-\U0001F1FF←-⇿️‍\s]+"
)


# ----------------------------------------------------------------------------
# Fonts (embedded base64 so the file works fully offline, incl. on a phone)
# ----------------------------------------------------------------------------
def font_face_css() -> str:
    def b64(path: Path) -> str:
        return base64.b64encode(path.read_bytes()).decode("ascii")

    font_dir = _font_dir()
    faces = []
    for fname, weight in [("InstrumentSans-Regular.ttf", 400), ("InstrumentSans-Bold.ttf", 700)]:
        p = font_dir / fname
        if p.exists():
            faces.append(
                f"@font-face{{font-family:'Instrument Sans';font-style:normal;font-weight:{weight};"
                f"font-display:swap;src:url(data:font/ttf;base64,{b64(p)}) format('truetype');}}"
            )
    cyr_range = ("unicode-range:U+0400-04FF,U+0500-052F,U+2DE0-2DFF,"
                 "U+A640-A69F,U+1C80-1C88,U+FE2E-FE2F;")
    for fname, weight in [("Inter-Cyrillic-400.woff2", 400), ("Inter-Cyrillic-700.woff2", 700)]:
        p = font_dir / fname
        if p.exists():
            faces.append(
                f"@font-face{{font-family:'Inter Cyrillic';font-style:normal;font-weight:{weight};"
                f"font-display:swap;{cyr_range}src:url(data:font/woff2;base64,{b64(p)}) format('woff2');}}"
            )
    return "\n".join(faces)


# ----------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------
def strip_lead_emoji(s: str) -> str:
    return _LEAD_EMOJI.sub("", s).strip()


def parse_frontmatter(text: str):
    meta = {}
    if text.startswith("---"):
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
            text = text[m.end():]
    return meta, text


def extract_tags(heading_line: str):
    """Pull `{priority=high status=now}` off a heading, return (clean, tags)."""
    tags = {}
    m = re.search(r"\{:?\s*([^}]*)\}\s*$", heading_line)
    if m:
        for pair in m.group(1).split():
            if "=" in pair:
                k, v = pair.split("=", 1)
                tags[k.strip().lower()] = v.strip().strip("'\"")
        heading_line = heading_line[: m.start()].rstrip()
    return heading_line, tags


def slugify(s: str, fallback: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower(), flags=re.UNICODE).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return s or fallback


def split_sections(body: str):
    """Split on top-level `## ` headings. Returns (preamble_md, [sections])."""
    lines = body.splitlines()
    preamble, sections, cur = [], [], None
    in_fence = False
    for line in lines:
        if re.match(r"^(```|~~~)", line.strip()):
            in_fence = not in_fence
        if not in_fence and re.match(r"^##\s+", line) and not re.match(r"^###", line):
            heading = re.sub(r"^##\s+", "", line)
            heading, tags = extract_tags(heading)
            clean = strip_lead_emoji(heading.strip())
            cur = {"title": clean or heading.strip(), "tags": tags, "lines": []}
            sections.append(cur)
        elif cur is None:
            preamble.append(line)
        else:
            cur["lines"].append(line)
    used = set()
    for i, s in enumerate(sections, 1):
        base = slugify(s["title"], f"section-{i}")
        sid, n = base, 2
        while sid in used:
            sid, n = f"{base}-{n}", n + 1
        used.add(sid)
        s["id"], s["num"] = sid, i
        s["body_md"] = "\n".join(s["lines"]).strip()
    return "\n".join(preamble).strip(), sections


CALLOUT_LABELS = {"decision": "Decision", "risk": "Risk",
                  "question": "Open question", "note": "Note"}


def render_callouts(md_html: str) -> str:
    def repl(m):
        inner = m.group(1)
        cm = re.match(r"\s*<p>\s*\[!(\w+)\]\s*", inner)
        if not cm:
            return f'<blockquote class="callout callout-note">{inner}</blockquote>'
        kind = cm.group(1).lower()
        label = CALLOUT_LABELS.get(kind, kind.capitalize())
        cls = kind if kind in CALLOUT_LABELS else "note"
        inner = inner[: cm.start()] + "<p>" + inner[cm.end():]
        return (f'<blockquote class="callout callout-{cls}">'
                f'<span class="callout-label">{label}</span>{inner}</blockquote>')
    return re.sub(r"<blockquote>(.*?)</blockquote>", repl, md_html, flags=re.DOTALL)


def no_orphan(s: str) -> str:
    """Bind the last two words with a non-breaking space to avoid a 1-word orphan."""
    parts = s.rsplit(" ", 1)
    return s if len(parts) < 2 else parts[0] + " " + parts[1]


def _wrap_tables(out: str) -> str:
    """Wrap tables; >=4 columns get a min-width so they scroll horizontally instead of cramming."""
    def repl(m):
        table = m.group(0)
        head = re.search(r"<tr>(.*?)</tr>", table, re.DOTALL)
        ncol = len(re.findall(r"<th", head.group(1) if head else table))
        if ncol >= 4:
            return f'<div class="table-wrap wide" style="--cols:{ncol}">{table}</div>'
        return f'<div class="table-wrap">{table}</div>'
    return re.sub(r"<table>.*?</table>", repl, out, flags=re.DOTALL)


def _deref_local(out: str) -> str:
    """Local/.md links aren't useful click targets in a portable HTML -> render as reference tokens."""
    def repl(m):
        href, text = m.group(1), m.group(2)
        if re.match(r"^(https?:|mailto:|tel:|#)", href, re.I):
            return m.group(0)
        return f'<span class="ref" title="{html.escape(href)}">{text}</span>'
    return re.sub(r'<a href="([^"]*)">(.*?)</a>', repl, out, flags=re.DOTALL)


def md(text: str) -> str:
    out = _MD.render(text)
    out = render_callouts(out)
    out = _wrap_tables(out)
    out = _deref_local(out)
    return out


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------
def chips_html(tags: dict) -> str:
    out = []
    pr = tags.get("priority", "").lower()
    if pr in PRIORITY_ORDER:
        out.append(f'<span class="chip chip-pr chip-pr-{pr}">{html.escape(pr)}</span>')
    st = tags.get("status")
    if st:
        out.append(f'<span class="chip chip-status">{html.escape(st)}</span>')
    return f'<span class="chips">{"".join(out)}</span>' if out else ""


def section_card(s: dict) -> str:
    return f"""
<section class="card" id="{s['id']}" data-sid="{s['id']}" data-label="{html.escape(s['title'])}">
  <header class="card-head">
    <h2 class="card-title"><span class="sec-no">§{s['num']}</span>{no_orphan(html.escape(s['title']))}</h2>
    {chips_html(s['tags'])}
  </header>
  <div class="prose">
{md(s['body_md'])}
  </div>
  <footer class="verdict" data-sid="{s['id']}">
    <div class="verdict-btns" role="group" aria-label="Verdict for {html.escape(s['title'])}">
      <button class="v-btn v-approve" data-v="approve">✓ Approve</button>
      <button class="v-btn v-rework" data-v="rework">✗ Rework</button>
    </div>
    <input class="note" type="text" placeholder="Add a note (optional)…" aria-label="Note">
  </footer>
</section>"""


def section_map(sections) -> str:
    return "\n".join(
        f'<a class="map-row" href="#{s["id"]}">'
        f'<span class="map-no">§{s["num"]}</span>'
        f'<span class="map-label">{html.escape(s["title"])}</span>'
        f'<span class="map-state" data-mapfor="{s["id"]}"></span></a>'
        for s in sections
    )


def _find_h1(body: str):
    """First `# ` heading OUTSIDE a code fence -> (text, start, end) or None.
    Fence-aware so a `# comment` inside a ``` block is never taken as the title."""
    in_fence = False
    pos = 0
    for line in body.splitlines(keepends=True):
        if line.strip().startswith(("```", "~~~")):
            in_fence = not in_fence
        elif not in_fence:
            m = re.match(r"^#\s+(.+?)\s*$", line)
            if m:
                return m.group(1), pos, pos + len(line)
        pos += len(line)
    return None


def build_html(src_path: Path) -> str:
    raw = src_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw)

    h1 = _find_h1(body)
    if meta.get("title"):
        title = meta["title"]
    elif h1:
        title = strip_lead_emoji(h1[0].strip()) or h1[0].strip()
    else:
        title = src_path.stem
    if h1 and not meta.get("title"):
        body = body[: h1[1]] + body[h1[2]:]

    date = meta.get("date")
    if not date:
        cm = re.search(r"Created\s+(\d{4}-\d{2}-\d{2})", body)
        date = cm.group(1) if cm else datetime.date.fromtimestamp(src_path.stat().st_mtime).isoformat()

    subtitle = meta.get("subtitle", "Direction review")
    project = meta.get("project", "")
    status = meta.get("status", "For review")

    preamble_md, sections = split_sections(body)
    preamble_md = re.sub(r"^>\s?", "", preamble_md, flags=re.MULTILINE)  # lead reads as plain abstract
    # doc_id keys per-doc localStorage feedback: stable across re-renders of the SAME doc,
    # but distinct for different docs (folds in filename + section structure) so verdicts/notes
    # never bleed between two docs that share a title+date.
    _struct = (src_path.stem + "|" + "\x00".join(s["id"] for s in sections)).encode("utf-8")
    doc_id = slugify(f"{title}-{date}", "review-doc") + "-" + hashlib.sha1(_struct).hexdigest()[:6]

    meta_bits = []
    if project:
        meta_bits.append(f'<span class="meta-item">{html.escape(project)}</span>')
    if date:
        meta_bits.append(f'<span class="meta-item">{html.escape(date)}</span>')
    if status:
        meta_bits.append(f'<span class="meta-item meta-status">{html.escape(status)}</span>')
    meta_row = '<span class="dot">·</span>'.join(meta_bits)

    lead_html = f'<div class="lead">{md(preamble_md)}</div>' if preamble_md else ""
    cards = "\n".join(section_card(s) for s in sections)

    tpl = TEMPLATE
    repl = {
        "/*__FONTS__*/": font_face_css(), "__DOCID__": doc_id,
        "__TITLE_DISPLAY__": no_orphan(html.escape(title)), "__TITLE__": html.escape(title),
        "__SUBTITLE__": html.escape(subtitle), "__METAROW__": meta_row,
        "<!--__LEAD__-->": lead_html, "<!--__MAP__-->": section_map(sections),
        "__SECCOUNT__": str(len(sections)), "<!--__SECTIONS__-->": cards,
    }
    for k, v in repl.items():
        tpl = tpl.replace(k, v)
    return tpl


# ----------------------------------------------------------------------------
# Template (CSS + JS + content markers). One place to edit the whole look.
# ----------------------------------------------------------------------------
TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>__TITLE__ · Review</title>
<script>
  try { var t = localStorage.getItem('rd-theme-__DOCID__') || 'dark';
        document.documentElement.setAttribute('data-theme', t); } catch(e){}
</script>
<style>
/*__FONTS__*/
:root{
  --cyan:#00F0F4; --magenta:#E560DF; --magenta-deep:#C031BA;
  --maxw:720px;
  --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:24px; --s6:32px; --s7:48px; --s8:64px;
  --font:'Instrument Sans','Inter Cyrillic',sans-serif;
}
:root[data-theme="dark"]{
  --bg:#02141B; --bg-deep:#010C11; --surface:#0B2027; --surface2:#103039;
  --border:#2C4047; --border2:#1E3036;
  --text:#D7DEE1; --muted:#90A4AE; --heading:#FFFFFF;
  --accent:#22F2F6; --accent-ink:#02141B; --action:#E560DF; --action-ink:#02141B;
  --code-bg:#0A2A33; --code-text:#9FECEE; --lead-bg:#0C262E; --ctl-border:#586F77;
}
:root[data-theme="light"]{
  --bg:#E7ECED; --bg-deep:#D8DFE1; --surface:#FFFFFF; --surface2:#F0F3F4;
  --border:#CDD5D7; --border2:#E0E6E7;
  --text:#1E2629; --muted:#546267; --heading:#08171D;
  --accent:#067A7D; --accent-ink:#FFFFFF; --action:#B0299F; --action-ink:#FFFFFF;
  --code-bg:#EEF2F3; --code-text:#055B5D; --lead-bg:#E2F5F5; --ctl-border:#90938C;
}
*{box-sizing:border-box;}
html{-webkit-text-size-adjust:100%;}
body{
  margin:0; background:var(--bg); color:var(--text);
  font-family:var(--font); font-size:16px; line-height:1.6; font-weight:400;
  padding-bottom:96px; transition:background .25s ease,color .25s ease;
}
.wrap{max-width:var(--maxw); margin:0 auto; padding:var(--s7) var(--s5) var(--s6);}
@media(max-width:600px){ .wrap{padding:var(--s6) var(--s4) var(--s5);} }

h1,h2,h3,h4{color:var(--heading); font-weight:700; line-height:1.18; margin:0; text-wrap:balance;}
h1{font-size:2rem; letter-spacing:-0.03em; line-height:1.12;}
h2{font-size:1.5rem; letter-spacing:-0.02em;}
h3{font-size:1rem; letter-spacing:-0.005em; line-height:1.3; margin-top:var(--s6); border-left:2px solid var(--accent); padding-left:var(--s3);}
h4{font-size:.95rem; margin-top:var(--s4);}
a{color:var(--action); text-decoration:none; border-bottom:1px solid color-mix(in srgb,var(--action) 38%,transparent);}
a:hover{border-bottom-color:var(--action);}

/* ---- Cover ---- */
.cover{border-bottom:1px solid var(--border); padding-bottom:var(--s6); margin-bottom:var(--s5);}
.eyebrow{font-size:12px; font-weight:700; letter-spacing:0.22em; text-transform:uppercase;
  color:var(--accent); margin-bottom:var(--s3);}
.cover h1{margin-bottom:var(--s4); text-wrap:pretty;}
.metarow{display:flex; flex-wrap:wrap; gap:var(--s2); align-items:center; color:var(--muted); font-size:14px;}
.metarow .dot{opacity:.5;}
.meta-status{color:var(--accent); font-weight:700;}
.lead{margin-top:var(--s5); padding:var(--s4) var(--s5); background:var(--lead-bg);
  border-left:3px solid var(--accent); color:var(--text); font-size:1rem; line-height:1.55;}
.lead p{margin:0;} .lead p + p{margin-top:var(--s3);}

/* ---- Section map ---- */
.mapwrap{margin-top:var(--s5); border:1px solid var(--border); background:var(--surface);}
.map-h{font-size:12px; font-weight:700; letter-spacing:0.16em; text-transform:uppercase;
  color:var(--muted); padding:var(--s3) var(--s4); border-bottom:1px solid var(--border2);}
.map-row{display:flex; align-items:center; gap:var(--s3); min-height:44px; padding:var(--s2) var(--s4);
  border-bottom:1px solid var(--border2); color:var(--text); text-decoration:none; transition:color .12s;}
.map-row:last-child{border-bottom:0;}
.map-row:hover{color:var(--heading);}
.map-row:hover .map-label{color:var(--accent);}
.map-no{color:var(--accent); font-weight:700; font-size:14px; min-width:32px;}
.map-label{flex:1; font-size:14px;}
.map-state{font-size:14px; font-weight:700; min-width:16px; text-align:right;}
.map-state.approve{color:var(--accent);} .map-state.rework{color:var(--action);}

/* ---- Section card ---- */
.card{border:1px solid var(--border); background:var(--surface); margin-top:var(--s5);
  padding:var(--s5); scroll-margin-top:var(--s4); transition:border-color .2s, box-shadow .2s;}
.card.is-approve{border-color:color-mix(in srgb,var(--accent) 60%,var(--border));}
.card.is-rework{border-color:color-mix(in srgb,var(--action) 60%,var(--border));}
.card-head{display:flex; justify-content:space-between; align-items:baseline; gap:var(--s3); margin-bottom:var(--s4);}
.card-title{min-width:0;}
.sec-no{color:var(--accent); font-weight:700; margin-right:var(--s2);}
.chips{display:flex; gap:var(--s2); flex-wrap:wrap; flex-shrink:0;}
.chip{font-size:12px; font-weight:700; letter-spacing:0.06em; text-transform:uppercase;
  padding:var(--s1) var(--s2); border:1px solid var(--border); color:var(--muted); white-space:nowrap;}
.chip-pr-high{color:var(--action); border-color:color-mix(in srgb,var(--action) 55%,transparent);}
.chip-pr-medium{color:var(--accent); border-color:color-mix(in srgb,var(--accent) 50%,transparent);}
.chip-status{color:var(--accent); border-color:color-mix(in srgb,var(--accent) 42%,transparent);}

/* ---- Prose ---- */
.prose{font-size:1rem;}
.prose > :first-child{margin-top:0;}
.prose p{margin:0 0 var(--s4); max-width:66ch;}
.prose ul,.prose ol{margin:0 0 var(--s4); padding-left:var(--s5);}
.prose li{margin:var(--s2) 0;}
.prose li > ul,.prose li > ol{margin:var(--s2) 0 var(--s2);}
.prose li::marker{color:var(--accent);}
.prose ul ul li::marker{color:var(--muted);}
.prose strong{color:var(--heading); font-weight:700;}
.prose code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.875em;
  background:var(--code-bg); color:var(--code-text); padding:1px var(--s1); border:1px solid var(--border2);
  overflow-wrap:anywhere;}
.ref{font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.875em; color:var(--muted);
  border-bottom:1px dotted color-mix(in srgb,var(--muted) 60%,transparent); overflow-wrap:anywhere; cursor:help;}
.ref::before{content:"↪\00a0"; color:var(--accent); border:0;}
.prose pre{background:var(--code-bg); border:1px solid var(--border); padding:var(--s4);
  overflow-x:auto; margin:0 0 var(--s4);}
.prose pre code{background:none; border:0; padding:0; white-space:pre; font-size:.8em; color:var(--code-text);}
.table-wrap{overflow-x:auto; margin:0 0 var(--s4); border:1px solid var(--border);}
.table-wrap.wide table{min-width:calc(var(--cols,4) * 150px);}
.prose table{width:100%; border-collapse:collapse; font-size:.875rem;}
.prose th,.prose td{border-bottom:1px solid var(--border2); border-right:1px solid var(--border2);
  padding:var(--s2) var(--s3); text-align:left; vertical-align:top; overflow-wrap:break-word; word-break:normal;}
.prose td:first-child,.prose th:first-child{white-space:nowrap;}
.prose th:last-child,.prose td:last-child{border-right:0;}
.prose tr:last-child td{border-bottom:0;}
.prose th{background:var(--surface2); color:var(--heading); font-weight:700;}
.prose blockquote{margin:0 0 var(--s4); padding:var(--s2) var(--s4); border-left:3px solid var(--border); color:var(--muted);}
.prose hr{border:0; border-top:1px solid var(--border); margin:var(--s5) 0;}

/* ---- Callouts ---- */
.callout{border-left:3px solid var(--accent); background:var(--surface2); padding:var(--s4); margin:0 0 var(--s4); color:var(--text);}
.callout-label{display:block; font-size:12px; font-weight:700; letter-spacing:0.14em; text-transform:uppercase; color:var(--accent); margin-bottom:var(--s2);}
.callout p{margin:0;} .callout p + p{margin-top:var(--s2);}
.callout-risk{border-left-color:var(--action);} .callout-risk .callout-label{color:var(--action);}

/* ---- Verdict controls ---- */
.verdict{display:flex; align-items:center; gap:var(--s3); flex-wrap:wrap;
  border-top:1px solid var(--border2); margin-top:var(--s5); padding-top:var(--s4);}
.verdict-btns{display:inline-flex; border:1px solid var(--ctl-border);}
.v-btn{font-family:var(--font); font-size:14px; font-weight:700; cursor:pointer; background:transparent;
  color:var(--muted); border:0; border-right:1px solid var(--ctl-border); padding:var(--s3) var(--s4);
  min-height:44px; transition:background .15s, color .15s;}
.v-btn:last-child{border-right:0;}
.v-btn:hover:not(.on){background:var(--surface2); color:var(--heading);}
.v-approve.on{background:var(--accent); color:var(--accent-ink);}
.v-rework.on{background:var(--action); color:var(--action-ink);}
.note{flex:1; min-width:128px; min-height:44px; font-family:var(--font); font-size:14px;
  background:var(--bg-deep); color:var(--text); border:1px solid var(--ctl-border); padding:var(--s3);}
.note:focus{outline:none; border-color:var(--accent);}
.note::placeholder{color:var(--muted);}

/* ---- Sticky footer: full-width surface, contents aligned to the doc column ---- */
.bar{position:fixed; left:0; right:0; bottom:0; z-index:50;
  background:var(--surface); border-top:1px solid var(--border);
  padding-bottom:env(safe-area-inset-bottom);}
.bar-in{max-width:var(--maxw); margin:0 auto;
  display:flex; align-items:center; gap:var(--s3); padding:var(--s3) var(--s5);}
.tally{font-size:14px; color:var(--muted); flex:1; line-height:1.35; min-width:0;}
.tally b{color:var(--heading);} .tally .t-ap{color:var(--accent);} .tally .t-rw{color:var(--action);}
.who{width:96px; font-family:var(--font); font-size:14px; min-height:44px;
  background:var(--bg-deep); color:var(--text); border:1px solid var(--ctl-border); padding:var(--s2) var(--s3);}
.who::placeholder{color:var(--muted);}
.icon-btn{cursor:pointer; background:transparent; border:1px solid var(--ctl-border); color:var(--text);
  font-size:14px; min-height:44px; min-width:44px; padding:var(--s2) var(--s3); font-family:var(--font);}
.icon-btn:hover{border-color:var(--muted);}
.copy-btn{cursor:pointer; font-family:var(--font); font-size:14px; font-weight:700; min-height:44px;
  background:var(--action); color:var(--action-ink); border:1px solid var(--action); padding:var(--s2) var(--s4);}
.copy-btn:hover{background:var(--magenta-deep); border-color:var(--magenta-deep);}
.copy-btn.done{background:var(--accent); border-color:var(--accent); color:var(--accent-ink);}
@media(max-width:600px){
  body{padding-bottom:128px;}
  .bar-in{flex-wrap:wrap; padding:var(--s3) var(--s4);}
  .tally{flex:1 1 100%; order:-1; white-space:nowrap;}
  .who{flex:1 1 auto; width:auto;} .copy-btn{padding:var(--s2) var(--s3);}
}
</style>
</head>
<body data-doc="__DOCID__" data-title="__TITLE__">
<div class="wrap">

  <div class="cover">
    <div class="eyebrow">__SUBTITLE__</div>
    <h1>__TITLE_DISPLAY__</h1>
    <div class="metarow">__METAROW__</div>
    <!--__LEAD__-->
    <div class="mapwrap">
      <div class="map-h">__SECCOUNT__ sections · tap to jump · mark each below</div>
      <!--__MAP__-->
    </div>
  </div>

  <!--__SECTIONS__-->

</div>

<div class="bar">
  <div class="bar-in">
    <div class="tally" id="tally"></div>
    <input class="who" id="who" type="text" placeholder="your name" aria-label="Reviewer name">
    <button class="icon-btn" id="themeBtn" title="Toggle light/dark" aria-label="Toggle theme">☾</button>
    <button class="copy-btn" id="copyBtn">Copy feedback</button>
  </div>
</div>

<script>
(function(){
  var DOC = document.body.dataset.doc, TITLE = document.body.dataset.title;
  var KEY = 'rd-' + DOC, THEMEKEY = 'rd-theme-' + DOC, state = {};
  try { state = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch(e){ state = {}; }
  if(!state.sections) state.sections = {};
  function save(){ try{ localStorage.setItem(KEY, JSON.stringify(state)); }catch(e){} }
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));

  function applyCard(card){
    var sid = card.dataset.sid, s = state.sections[sid] || {};
    card.classList.toggle('is-approve', s.v === 'approve');
    card.classList.toggle('is-rework', s.v === 'rework');
    card.querySelector('.v-approve').classList.toggle('on', s.v === 'approve');
    card.querySelector('.v-rework').classList.toggle('on', s.v === 'rework');
    var map = document.querySelector('.map-state[data-mapfor="'+sid+'"]');
    if(map){ map.className = 'map-state ' + (s.v||''); map.textContent = s.v==='approve'?'✓':(s.v==='rework'?'✗':''); }
  }
  function tally(){
    var ap=0, rw=0, total=cards.length;
    cards.forEach(function(c){ var v=(state.sections[c.dataset.sid]||{}).v; if(v==='approve')ap++; else if(v==='rework')rw++; });
    document.getElementById('tally').innerHTML =
      '<b>'+ap+'</b> <span class="t-ap">approved</span> · <b>'+rw+'</b> <span class="t-rw">rework</span> · <b>'+(total-ap-rw)+'</b> untouched';
  }
  cards.forEach(function(card){
    var sid = card.dataset.sid, s = state.sections[sid] || (state.sections[sid] = {});
    var note = card.querySelector('.note');
    if(s.note) note.value = s.note;
    applyCard(card);
    card.querySelectorAll('.v-btn').forEach(function(btn){
      btn.addEventListener('click', function(){
        var v = btn.dataset.v; s.v = (s.v === v) ? null : v; save(); applyCard(card); tally();
      });
    });
    note.addEventListener('input', function(){ s.note = note.value.trim(); save(); });
  });
  var who = document.getElementById('who');
  if(state.who) who.value = state.who;
  who.addEventListener('input', function(){ state.who = who.value.trim(); save(); });

  function syncIcon(){ document.getElementById('themeBtn').textContent =
      document.documentElement.getAttribute('data-theme')==='dark' ? '☾' : '☀'; }
  syncIcon();
  document.getElementById('themeBtn').addEventListener('click', function(){
    var t = document.documentElement.getAttribute('data-theme')==='dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', t);
    try{ localStorage.setItem(THEMEKEY, t); }catch(e){} syncIcon();
  });

  function buildFeedback(){
    var lines = ['Review: ' + TITLE + (state.who ? ' · ' + state.who : '')];
    cards.forEach(function(c){
      var s = state.sections[c.dataset.sid] || {};
      if(!s.v && !s.note) return;
      var verdict = s.v==='approve' ? '✓ approve' : (s.v==='rework' ? '✗ rework' : '·');
      lines.push(c.querySelector('.sec-no').textContent + ' ' + c.dataset.label +
        '   ' + verdict + (s.note ? ' — "' + s.note + '"' : ''));
    });
    if(lines.length === 1) lines.push('(no sections marked yet)');
    return lines.join('\n');
  }
  document.getElementById('copyBtn').addEventListener('click', function(){
    var txt = buildFeedback(), btn = this;
    function ok(){ btn.classList.add('done'); btn.textContent='Copied ✓';
      setTimeout(function(){ btn.classList.remove('done'); btn.textContent='Copy feedback'; }, 1600); }
    function fallback(){ var ta=document.createElement('textarea'); ta.value=txt; document.body.appendChild(ta);
      ta.select(); try{ document.execCommand('copy'); ok(); }catch(e){ alert(txt); } document.body.removeChild(ta); }
    if(navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(ok, fallback);
    else fallback();
  });
  tally();
})();
</script>
</body>
</html>"""


_BUNDLE_TO_APP = {
    "com.google.chrome": "Google Chrome", "com.brave.browser": "Brave Browser",
    "com.microsoft.edgemac": "Microsoft Edge", "org.chromium.chromium": "Chromium",
    "com.vivaldi.vivaldi": "Vivaldi", "company.thebrowser.browser": "Arc",
    "com.apple.safari": "Safari",
}


def _default_browser_app():
    """The macOS default https-handler app name (so we open the user's real browser), or None."""
    try:
        import plistlib
        p = Path.home() / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
        data = plistlib.loads(p.read_bytes())
        for h in data.get("LSHandlers", []):
            if h.get("LSHandlerURLScheme") == "https":
                return _BUNDLE_TO_APP.get((h.get("LSHandlerRoleAll") or "").lower())
    except Exception:
        pass
    return None


def open_in_browser(path: Path) -> str:
    """Open `path` in a NEW browser window (default after a render), preferring the
    user's DEFAULT browser. Override with $REVIEWDOC_BROWSER. `--no-open` suppresses."""
    url = path.as_uri()
    if sys.platform == "darwin":
        chosen = os.environ.get("REVIEWDOC_BROWSER") or _default_browser_app()

        def safari_new_window():
            subprocess.run([
                "osascript",
                "-e", f'tell application "Safari" to make new document with properties {{URL:"{url}"}}',
                "-e", 'tell application "Safari" to activate',
            ], check=False)

        if chosen == "Safari" and Path("/Applications/Safari.app").exists():
            safari_new_window()
            return "Safari"
        ordered = []
        for app in [chosen, "Google Chrome", "Brave Browser", "Microsoft Edge", "Vivaldi", "Chromium", "Arc"]:
            if app and app != "Safari" and app not in ordered:
                ordered.append(app)
        for app in ordered:
            if Path(f"/Applications/{app}.app").exists():
                subprocess.run(["open", "-na", app, "--args", "--new-window", url], check=False)
                return app
        if Path("/Applications/Safari.app").exists():
            safari_new_window()
            return "Safari"
        subprocess.run(["open", str(path)], check=False)
        return "default browser"
    if sys.platform.startswith("linux"):
        subprocess.run(["xdg-open", str(path)], check=False)
        return "xdg-open"
    import webbrowser
    webbrowser.open(url, new=1)
    return "default browser"


def main():
    argv = sys.argv[1:]
    positionals = [a for a in argv if not a.startswith("-")]
    if "-h" in argv or "--help" in argv or not positionals:
        print(__doc__)
        return 0
    no_open = "--no-open" in argv
    args = [a for a in argv if a != "--no-open"]
    out = None
    if "-o" in args:
        i = args.index("-o")
        out, args = args[i + 1], args[:i] + args[i + 2:]
    src = Path(args[0]).resolve()
    if not src.exists():
        print(f"error: {src} not found", file=sys.stderr)
        return 1
    out_path = Path(out).resolve() if out else src.with_suffix(".html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_html(src), encoding="utf-8")
    print(f"✓ {out_path}  ({out_path.stat().st_size / 1024:.0f} KB, self-contained)")
    if not no_open:
        where = open_in_browser(out_path)
        print(f"↗ opened in {where} (new window)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
