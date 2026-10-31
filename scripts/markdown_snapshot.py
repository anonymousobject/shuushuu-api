#!/usr/bin/env python3
"""
Render every comment, private message and forum post to JSONL, and compare two
such snapshots. Used to prove a markdown parser change against real content.

Usage:
    uv run python scripts/markdown_snapshot.py render --out FILE [--version N]
    uv run python scripts/markdown_snapshot.py gate MAIN V1 --report FILE
    uv run python scripts/markdown_snapshot.py compare A B --report FILE [--normalise-newlines]

`render` reads the database named by DATABASE_URL, read-only, with a
server-side cursor. It renders each row the way the API does (comments and PMs
through normalize_legacy_entities; forum posts with headings at version 2). When
parse_markdown has no `version` parameter (older code), it calls
parse_markdown(text), so the same file runs on `main`.

The reports contain private-message text. Never commit or post them.
"""

import argparse
import asyncio
import inspect
import json
import os
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import asyncpg  # type: ignore[import-untyped]

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils.markdown import normalize_legacy_entities, parse_markdown

# (kind, table, id column, text column, normalise first)
SOURCES = [
    ("comment", "posts", "post_id", "post_text", True),
    ("pm", "privmsgs", "privmsg_id", "text", True),
    ("forum", "forum_posts", "post_id", "post_text", False),
]
BATCH = 2000
EXAMPLES_PER_GROUP = 20
EXCERPT = 200
OLD_QUOTE_CAP = 10  # version 1 resolved at most this many quote nesting levels


def normalise_newlines(html: str) -> str:
    """Drop the newlines version 2 does not emit: after <br>, around blockquotes."""
    html = html.replace("<br>\n", "<br>")
    return re.sub(r"\n?(</?blockquote>)\n?", r"\1", html)


_LIST_LINE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S", re.M)
_STAR_PAIR = re.compile(r"^[ \t]*\*[ \t]+.*\n[ \t]*\*[ \t]+", re.M)
_QUOTE_FIRST_GT = re.compile(r"\[quote(?:=[^\]]*)?\]\s*>", re.I)
_NAMED_QUOTE = re.compile(r'\[quote="([^"]*)"\]', re.I)
_NAME_MARKUP = re.compile(r"[*_`\[<]|https?:|www\.", re.I)
_QUOTE_TAG = re.compile(r"\[(/?)quote(?:=[^\]]*)?\]", re.I)
_CONTAINER_TAG = re.compile(r"\[(/?)(quote|spoiler)(?:=[^\]]*)?\]", re.I)
_QUOTE_LINE = re.compile(r"^[ \t]*>", re.M)
# A line that starts like a quote but is a face: ">", up to three characters, "<".
_FACE_LINE = re.compile(r"^[ \t]*>[^\s<]{0,3}<", re.M)
_INLINE_MARKERS = ("**", "*", "_")


def _max_quote_depth(source: str) -> int:
    depth = peak = 0
    for m in _QUOTE_TAG.finditer(source):
        depth = max(depth - 1, 0) if m.group(1) else depth + 1
        peak = max(peak, depth)
    return peak


def _quote_line_spans_container(source: str) -> bool:
    for line in source.splitlines():
        if not line.lstrip().startswith(">"):
            continue
        balance: dict[str, int] = defaultdict(int)
        for m in _CONTAINER_TAG.finditer(line):
            balance[m.group(2).lower()] += -1 if m.group(1) else 1
        if any(balance.values()):
            return True
    return False


def _inline_crosses_lines(source: str) -> bool:
    """A marker is paired across lines when some line holds an odd number of it."""
    lines = source.splitlines()
    for marker in _INLINE_MARKERS:
        counts = [line.count(marker) for line in lines]
        if sum(counts) >= 2 and any(c % 2 for c in counts):
            return True
    return False


def _has_quotes(source: str) -> bool:
    return bool(_QUOTE_LINE.search(source) or _QUOTE_TAG.search(source))


def _unmatched_named_quote(source: str) -> bool:
    opens = len(re.findall(r'\[quote="', source, re.I))
    return opens > len(re.findall(r"\[/quote\]", source, re.I))


def _quote_name_markup(source: str) -> bool:
    return any(_NAME_MARKUP.search(name) for name in _NAMED_QUOTE.findall(source))


_DETECTORS: dict[str, Callable[[str], bool]] = {
    "face": lambda s: bool(_FACE_LINE.search(s)),
    "list-line": lambda s: bool(_LIST_LINE.search(s)),
    "star-pair": lambda s: bool(_STAR_PAIR.search(s)),
    "code-span": lambda s: "`" in s,
    "code-block": lambda s: bool(re.search(r"^[ \t]*(```|~~~)", s, re.M)),
    "quote-depth": lambda s: _max_quote_depth(s) > OLD_QUOTE_CAP,
    "quote-first-line-gt": lambda s: bool(_QUOTE_FIRST_GT.search(s)),
    "inline-across-quote": lambda s: _has_quotes(s) and _inline_crosses_lines(s),
    "unmatched-named-quote": _unmatched_named_quote,
    "quote-name-markup": _quote_name_markup,
    "quote-line-container": _quote_line_spans_container,
}


def causes(source: str) -> set[str]:
    """Heuristically classify why version 2 may render `source` differently."""
    return {name for name, detect in _DETECTORS.items() if detect(source)}


def _renderer(version: int | None) -> Callable[[str, str | None, bool], str]:
    takes_version = "version" in inspect.signature(parse_markdown).parameters

    def render(kind: str, text: str | None, normalise: bool) -> str:
        if normalise:
            text = normalize_legacy_entities(text)
        text = text or ""
        if not takes_version:
            return parse_markdown(text)  # type: ignore[call-arg]
        v = 1 if version is None else version
        if kind == "forum" and v == 2:
            return parse_markdown(text, v, headings=True)
        return parse_markdown(text, v)

    return render


def _dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        from app.config import settings

        url = settings.DATABASE_URL
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def render_all(out: Path, version: int | None) -> None:
    render = _renderer(version)
    conn = await asyncpg.connect(_dsn())
    try:
        with out.open("w", encoding="utf-8") as fh:
            for kind, table, id_col, text_col, normalise in SOURCES:
                count = 0
                async with conn.transaction(readonly=True):
                    query = f"SELECT {id_col}, {text_col} FROM {table} ORDER BY {id_col}"
                    async for row in conn.cursor(query, prefetch=BATCH):
                        html = render(kind, row[1], normalise)
                        fh.write(json.dumps({"kind": kind, "id": row[0], "html": html}) + "\n")
                        count += 1
                print(f"{kind}: {count} rows", file=sys.stderr)
    finally:
        await conn.close()


def _read(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            yield json.loads(line)


async def _fetch_sources(keys: set[tuple[str, int]]) -> dict[tuple[str, int], str]:
    """Fetch source text for the changed rows only."""
    conn = await asyncpg.connect(_dsn())
    sources: dict[tuple[str, int], str] = {}
    try:
        async with conn.transaction(readonly=True):
            for kind, table, id_col, text_col, normalise in SOURCES:
                ids = [i for k, i in keys if k == kind]
                if not ids:
                    continue
                rows = await conn.fetch(
                    f"SELECT {id_col}, {text_col} FROM {table} WHERE {id_col} = ANY($1)",
                    ids,
                )
                for r in rows:
                    text = normalize_legacy_entities(r[1]) if normalise else r[1]
                    sources[(kind, r[0])] = text or ""
    finally:
        await conn.close()
    return sources


def _first_diff(a: str, b: str) -> int:
    return next(
        (i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b))
    )


def _excerpt(text: str, centre: int, width: int) -> str:
    """`width` characters of `text` around `centre`, with ... where cut."""
    start = max(0, min(centre - width // 2, len(text) - width))
    end = start + width
    return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "")


def _fence(text: str) -> str:
    return "```\n" + text.replace("```", "'''") + "\n```"


def _cr_only(before: str, after: str) -> bool:
    """True when the outputs differ only by carriage returns (version 2 drops them)."""
    return normalise_newlines(before.replace("\r", "")) == normalise_newlines(after)


def _row_causes(source: str, before: str, after: str, normalise: bool) -> list[str]:
    if normalise and _cr_only(before, after):
        return ["cr-only"]
    return sorted(causes(source)) or ["unexplained"]


def _changed_rows(
    a: Path, b: Path, normalise: bool
) -> tuple[list[tuple[str, int, str, str]], dict[str, int]]:
    changed: list[tuple[str, int, str, str]] = []
    totals: dict[str, int] = defaultdict(int)
    for ra, rb in zip(_read(a), _read(b), strict=True):
        if (ra["kind"], ra["id"]) != (rb["kind"], rb["id"]):
            raise SystemExit(f"row mismatch: {ra['kind']} {ra['id']} vs {rb['kind']} {rb['id']}")
        totals[ra["kind"]] += 1
        before, after = ra["html"], rb["html"]
        if normalise:
            before, after = normalise_newlines(before), normalise_newlines(after)
        if before != after:
            changed.append((ra["kind"], ra["id"], before, after))
    return changed, totals


def compare(a: Path, b: Path, report: Path, normalise: bool) -> None:
    changed, totals = _changed_rows(a, b, normalise)
    sources = asyncio.run(_fetch_sources({(k, i) for k, i, _, _ in changed}))
    per_kind: dict[str, int] = defaultdict(int)
    groups: dict[str, list[tuple[str, int, str, str, str]]] = defaultdict(list)
    group_counts: dict[str, int] = defaultdict(int)
    for kind, id_, before, after in changed:
        per_kind[kind] += 1
        source = sources.get((kind, id_), "")
        for cause in _row_causes(source, before, after, normalise):
            group_counts[cause] += 1
            if len(groups[cause]) < EXAMPLES_PER_GROUP:
                groups[cause].append((kind, id_, source, before, after))

    order = sorted(group_counts, key=lambda c: -group_counts[c])
    lines = [f"# Markdown snapshot comparison\n\nA: `{a}`\nB: `{b}`\n"]
    lines.append(f"Newline normalisation: {'on' if normalise else 'off'}\n")
    lines.append("## Changed rows per kind\n")
    lines.extend(f"- {kind}: {per_kind[kind]} of {totals[kind]}" for kind, *_ in SOURCES)
    lines.append(f"\nTotal changed: {len(changed)}\n")
    lines.append("## Groups (a row may appear in several)\n")
    lines.extend(f"- {cause}: {group_counts[cause]}" for cause in order)
    for cause in order:
        lines.append(f"\n## {cause} ({group_counts[cause]})\n")
        for kind, id_, source, before, after in groups[cause]:
            lines.append(f"### {kind} {id_}\n")
            at = _first_diff(before, after)
            # Escaping lengthens the HTML, so scale the offset to find the source spot.
            source_at = at * len(source) // max(len(before), 1)
            lines.append("Source:\n" + _fence(_excerpt(source, source_at, EXCERPT)))
            lines.append("Before:\n" + _fence(_excerpt(before, at, EXCERPT * 2)))
            lines.append("After:\n" + _fence(_excerpt(after, at, EXCERPT * 2)) + "\n")
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"changed {len(changed)} rows; groups: {dict(group_counts)}", file=sys.stderr)


def _face_off_renderer() -> Callable[[str, str | None, bool], str]:
    """Version 1 render with the face check forced off, i.e. main's behaviour."""
    render = _renderer(1)

    def render_without_face(kind: str, text: str | None, normalise: bool) -> str:
        with patch("app.utils.markdown_v1.is_face_line", return_value=False):
            return render(kind, text, normalise)

    return render_without_face


def gate_failures(
    changed: list[tuple[str, int, str, str]],
    sources: dict[tuple[str, int], str],
    render_without_face: Callable[[str, str | None, bool], str],
    main_html: dict[tuple[str, int], str],
) -> list[tuple[str, int]]:
    """Rows whose face-off version 1 output differs from main's: not 'face fix only'."""
    normalise = {kind: n for kind, _, _, _, n in SOURCES}
    failures = []
    for kind, id_, _, _ in changed:
        key = (kind, id_)
        if render_without_face(kind, sources.get(key, ""), normalise[kind]) != main_html[key]:
            failures.append(key)
    return failures


def gate(a: Path, b: Path, report: Path) -> bool:
    """Prove every row that differs between main (a) and version 1 (b) is the face fix alone."""
    changed, _ = _changed_rows(a, b, False)
    wanted = {(k, i) for k, i, _, _ in changed}
    main_html = {
        (r["kind"], r["id"]): r["html"] for r in _read(a) if (r["kind"], r["id"]) in wanted
    }
    sources = asyncio.run(_fetch_sources(wanted))
    failures = gate_failures(changed, sources, _face_off_renderer(), main_html)
    proven = len(changed) - len(failures)
    lines = ["# Gate: changed rows are the face fix only\n"]
    lines.append(f"Changed rows: {len(changed)}")
    lines.append(f"Proven face-fix-only (face-off render equals main byte for byte): {proven}")
    lines.append(f"Failures: {len(failures)}\n")
    lines.extend(f"- {kind} {id_}" for kind, id_ in failures)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"changed {len(changed)}, proven {proven}, failures {len(failures)}", file=sys.stderr)
    return not failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--version", type=int, default=None)
    c = sub.add_parser("compare")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    c.add_argument("--report", type=Path, required=True)
    c.add_argument("--normalise-newlines", action="store_true")
    g = sub.add_parser("gate")
    g.add_argument("a", type=Path, help="main snapshot")
    g.add_argument("b", type=Path, help="branch version 1 snapshot")
    g.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.cmd == "gate":
        sys.exit(0 if gate(args.a, args.b, args.report) else 1)
    if args.cmd == "render":
        asyncio.run(render_all(args.out, args.version))
    else:
        compare(args.a, args.b, args.report, args.normalise_newlines)


if __name__ == "__main__":
    main()
