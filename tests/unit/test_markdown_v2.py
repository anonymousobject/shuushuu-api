"""
Tests for version 2 of the markdown renderer
"""

import time

import pytest

from app.utils.markdown_v1 import render_v1
from app.utils.markdown_v2 import _render_inline, _Tokens, render_v2
from tests.unit.test_markdown import RENDER_TIME_LIMIT_SECONDS

# Text that uses only version 1 syntax and none of the design's expected
# differences renders the same in both versions.
PARITY_CORPUS = [
    "This is **bold** text",
    "*italic*",
    "**bold** and *italic*",
    "[example](https://example.com)",
    "[x](javascript:alert(1))",
    "[search](https://example.com/search?q=test&page=2)",
    "see https://example.com/a_(b). ok",
    "**https://example.com**",
    "> a",
    "> a\n> b",
    "> a\nb",
    "x\n> a",
    "x\n\n> a",
    "Line 1\nLine 2",
    "a\n\nb",
    "a\n",
    "\na",
    "\n\n",
    "<script>alert('xss')</script>",
    "&quot;hi&quot; &amp; bye",
    "*a\nb*",
    "[link\ntext](https://example.com)",
    ">_< oh no",
    "a\n>_<\nb",
    "* nods head* yes",
    "- -",
    "- _ -",
    "- ",
    "I don`t think that`s snow",
    "`_`",
    "``x``",
    "```\na",
    "1234567890. x",
    "> - a",
    "#1 fan!",
    "# x",
    "#### x",
]


@pytest.mark.parametrize("text", PARITY_CORPUS)
def test_matches_version_1(text):
    assert render_v2(text, headings=False) == render_v1(text)


def test_crlf_is_normalised():
    assert render_v2("a\r\nb\rc", headings=False) == "a<br>\nb<br>\nc"


def test_placeholder_code_points_are_neutralised():
    assert render_v2("x\ue0000\ue001y", headings=False) == "x\ufffd0\ufffdy"


def test_placeholder_entities_are_neutralised():
    # Unescaping a stored entity can produce a placeholder code point too
    assert render_v2("x&#xe000;0&#xe001;y", headings=False) == "x\ufffd0\ufffdy"


def test_empty():
    assert render_v2("", headings=False) == "" and render_v2(None, headings=False) == ""


@pytest.mark.parametrize(
    "text",
    ["[" * 40000, "[" * 40000 + "](", "[x](" * 20000],
    ids=[
        "40000-open-brackets",
        "40000-open-brackets-one-close-and-paren",
        "20000-links-without-closing-paren",
    ],
)
def test_bracket_runs_are_linear(text):
    start = time.perf_counter()
    render_v2(text, headings=False)
    assert time.perf_counter() - start < RENDER_TIME_LIMIT_SECONDS


def test_inline_rules_skip_tokens():
    tokens = _Tokens()
    token = tokens.add("<code>*x*</code>")
    html = _render_inline(f"*a{token}b* **c**")
    assert tokens.expand(html) == "*a<code>*x*</code>b* <strong>c</strong>"
