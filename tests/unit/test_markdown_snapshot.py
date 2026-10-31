"""Tests for the markdown compatibility snapshot helpers."""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "markdown_snapshot", Path(__file__).parents[2] / "scripts" / "markdown_snapshot.py"
)
assert _SPEC and _SPEC.loader
snapshot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(snapshot)


class TestNormaliseNewlines:
    def test_br_newline(self) -> None:
        assert snapshot.normalise_newlines("a<br>\nb") == "a<br>b"

    def test_newlines_next_to_blockquote(self) -> None:
        assert snapshot.normalise_newlines("\n<blockquote>x\n</blockquote>\n") == (
            "<blockquote>x</blockquote>"
        )

    def test_other_newlines_unchanged(self) -> None:
        assert snapshot.normalise_newlines("a\nb") == "a\nb"


class TestCauses:
    def test_list_line(self) -> None:
        assert snapshot.causes("- a") == {"list-line"}

    def test_star_pair(self) -> None:
        assert snapshot.causes("* a\n* b") == {"star-pair", "list-line"}

    def test_code_span(self) -> None:
        assert snapshot.causes("`x`") == {"code-span"}

    def test_code_block(self) -> None:
        assert snapshot.causes("```\nx\n```") == {"code-block", "code-span"}

    def test_quote_first_line_gt(self) -> None:
        assert snapshot.causes("[quote]> x[/quote]") == {"quote-first-line-gt"}

    def test_quote_name_markup(self) -> None:
        assert "quote-name-markup" in snapshot.causes('[quote="*bob*"]x[/quote]')

    def test_quote_depth(self) -> None:
        assert "quote-depth" in snapshot.causes("[quote]" * 11 + "x" + "[/quote]" * 11)

    def test_unmatched_named_quote(self) -> None:
        assert "unmatched-named-quote" in snapshot.causes('[quote="bob"]x')

    def test_quote_line_container(self) -> None:
        assert "quote-line-container" in snapshot.causes("> [spoiler]a\nb[/spoiler]")

    def test_inline_across_quote(self) -> None:
        assert "inline-across-quote" in snapshot.causes("*a\n> b*")

    def test_face(self) -> None:
        assert snapshot.causes(">_< hi there") == {"face"}

    def test_face_dot(self) -> None:
        assert snapshot.causes(">.< ouch") == {"face"}

    def test_plain(self) -> None:
        assert snapshot.causes("plain") == set()


class TestCrOnly:
    def test_cr_before_br(self) -> None:
        assert snapshot._cr_only("a\r<br>\nb", "a<br>\nb")

    def test_real_difference(self) -> None:
        assert not snapshot._cr_only("a\r<br>\nb", "<ul><li>a</li></ul>")
