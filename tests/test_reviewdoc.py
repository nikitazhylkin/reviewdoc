"""Tests for the reviewdoc generator. Run: python3 -m pytest tests/ -q"""
import importlib.util
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build.py"
_spec = importlib.util.spec_from_file_location("reviewdoc_build", BUILD)
rd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rd)


# ---- unit: parsing helpers -------------------------------------------------
def test_strip_lead_emoji():
    assert rd.strip_lead_emoji("🔴 ABSOLUTE HARD RULES") == "ABSOLUTE HARD RULES"
    assert rd.strip_lead_emoji("Plain heading") == "Plain heading"
    assert rd.strip_lead_emoji("Запуск проєкту") == "Запуск проєкту"  # cyrillic untouched


def test_extract_tags():
    clean, tags = rd.extract_tags("Build {priority=high status=now}")
    assert clean == "Build"
    assert tags == {"priority": "high", "status": "now"}
    clean2, tags2 = rd.extract_tags("No tags here")
    assert clean2 == "No tags here" and tags2 == {}


def test_slugify_cyrillic_and_dedup():
    assert rd.slugify("Source of truth", "x") == "source-of-truth"
    assert rd.slugify("договір §1.4", "x")  # non-empty, no crash on cyrillic
    assert rd.slugify("!!!", "fallback") == "fallback"


def test_parse_frontmatter():
    meta, body = rd.parse_frontmatter("---\ntitle: Hi\nproject: X\n---\nbody here")
    assert meta["title"] == "Hi" and meta["project"] == "X"
    assert body.strip() == "body here"
    meta2, body2 = rd.parse_frontmatter("no frontmatter")
    assert meta2 == {} and body2 == "no frontmatter"


def test_no_orphan_binds_last_two_words_with_nbsp():
    out = rd.no_orphan("Master Implementation Plan")
    assert " " in out
    assert out.endswith("Implementation Plan")
    assert rd.no_orphan("Single") == "Single"  # nothing to bind


# ---- unit: markdown post-processing ----------------------------------------
def test_wrap_tables_wide_vs_narrow():
    wide = rd._wrap_tables("<table><thead><tr><th>a<th>b<th>c<th>d<th>e</tr></thead></table>")
    assert 'class="table-wrap wide"' in wide and "--cols:5" in wide
    narrow = rd._wrap_tables("<table><thead><tr><th>a<th>b</tr></thead></table>")
    assert 'class="table-wrap"' in narrow and "wide" not in narrow


def test_deref_local_links():
    out = rd._deref_local('<a href="LEARNED.md">LEARNED.md</a> '
                          '<a href="https://x.com">site</a> '
                          '<a href="#anchor">jump</a>')
    assert '<span class="ref" title="LEARNED.md">LEARNED.md</span>' in out
    assert '<a href="https://x.com">site</a>' in out      # real URL kept
    assert '<a href="#anchor">jump</a>' in out            # in-page anchor kept


def test_render_callouts():
    out = rd.render_callouts("<blockquote>\n<p>[!decision] Ship it.</p>\n</blockquote>")
    assert 'class="callout callout-decision"' in out
    assert "Decision" in out and "[!decision]" not in out


def test_md_nests_two_space_sublists():
    src = ("- parent item\n"
           "  - (a) child one\n"
           "  - (b) child two\n"
           "- second parent\n")
    out = rd.md(src)
    assert out.count("<ul>") >= 2          # outer + nested
    assert "(a) child one" in out


def test_md_table_and_callout_roundtrip():
    out = rd.md("| A | B |\n|---|---|\n| 1 | 2 |\n\n> [!risk] careful")
    assert "table-wrap" in out and "<table>" in out
    assert 'callout-risk' in out


# ---- integration: full render ----------------------------------------------
SAMPLE = """---
title: Test Plan For Review
project: DEMO
status: For review
---
Intro abstract line that becomes the lead.

## Discovery {priority=high status=now}
Body with a [LEARNED.md](LEARNED.md) ref and a [site](https://x.com).

- bullet one
  - nested a
  - nested b

## Build
> [!decision] We ship server-rendered.

| # | App | Notes | TZ | Effort |
|---|-----|-------|----|--------|
| 1 | Foo | longish note here | §2 | S |
"""


def test_build_html_smoke(tmp_path):
    src = tmp_path / "plan.md"
    src.write_text(SAMPLE, encoding="utf-8")
    out = rd.build_html(src)
    # structure
    assert out.count('class="card"') == 2                 # two ## sections
    assert '<span class="sec-no">§1</span>' in out        # inline section number
    assert "table-wrap wide" in out                       # 5-col table scrolls
    assert 'class="ref"' in out                           # .md link -> ref token
    assert '<a href="https://x.com"' in out               # real link kept
    assert "callout-decision" in out                      # callout rendered
    assert out.count("@font-face") == 4                   # fonts embedded
    assert "🔴" not in out
    # no unreplaced template markers
    for marker in ("__TITLE__", "__TITLE_DISPLAY__", "__DOCID__", "/*__FONTS__*/",
                   "<!--__SECTIONS__-->", "<!--__MAP__-->", "__SECCOUNT__"):
        assert marker not in out, f"unreplaced marker {marker}"


def test_main_no_open_does_not_launch_browser(tmp_path, monkeypatch):
    src = tmp_path / "p.md"
    src.write_text(SAMPLE, encoding="utf-8")
    out = tmp_path / "p.html"
    calls = []
    monkeypatch.setattr(rd, "open_in_browser", lambda p: calls.append(p))
    monkeypatch.setattr(sys, "argv", ["build.py", str(src), "-o", str(out), "--no-open"])
    assert rd.main() == 0
    assert out.exists() and out.stat().st_size > 10_000
    assert calls == []                                    # browser NOT launched


def test_main_default_opens_browser(tmp_path, monkeypatch):
    src = tmp_path / "p.md"
    src.write_text(SAMPLE, encoding="utf-8")
    out = tmp_path / "p.html"
    calls = []
    monkeypatch.setattr(rd, "open_in_browser", lambda p: calls.append(p))
    monkeypatch.setattr(sys, "argv", ["build.py", str(src), "-o", str(out)])
    assert rd.main() == 0
    assert len(calls) == 1                                # auto-open is the default


# ---- regression: verification findings -------------------------------------
import re as _re


def test_h1_detection_is_fence_aware(tmp_path):
    src = tmp_path / "deploy.md"
    src.write_text("```bash\n# Install deps\nnpm i\n```\n\n# Real Title\n\n## Section\nbody\n",
                   encoding="utf-8")
    out = rd.build_html(src)
    assert "<title>Real Title · Review</title>" in out    # real H1 used, not the fenced comment
    assert "# Install deps" in out                        # fenced comment NOT stripped
    assert out.count("<h1>") == 1


def test_h1_fallback_keeps_fenced_comment(tmp_path):
    src = tmp_path / "myplan.md"
    src.write_text("```bash\n# just a comment\necho hi\n```\n\n## Section\nbody\n", encoding="utf-8")
    out = rd.build_html(src)
    assert "<h1>myplan</h1>" in out                       # title falls back to filename stem
    assert "# just a comment" in out                      # not stripped from the code block


def test_doc_id_distinguishes_docs_and_is_stable(tmp_path):
    a = tmp_path / "billing.md"
    a.write_text("---\ntitle: Plan\ndate: 2026-06-20\n---\n## Billing\nx\n", encoding="utf-8")
    b = tmp_path / "auth.md"
    b.write_text("---\ntitle: Plan\ndate: 2026-06-20\n---\n## Auth\ny\n", encoding="utf-8")
    da = _re.search(r'data-doc="([^"]+)"', rd.build_html(a)).group(1)
    db = _re.search(r'data-doc="([^"]+)"', rd.build_html(b)).group(1)
    assert da != db                                       # same title+date, different docs -> different key
    assert da == _re.search(r'data-doc="([^"]+)"', rd.build_html(a)).group(1)  # stable on re-render


def test_emoji_only_heading_keeps_label(tmp_path):
    src = tmp_path / "p.md"
    src.write_text("## 🔴🔥\nbody\n", encoding="utf-8")
    out = rd.build_html(src)
    assert 'data-label=""' not in out                     # section stays identifiable
    assert "🔴🔥" in out                                   # falls back to the raw heading
