#!/usr/bin/env python3
"""
Generate Danbooru-character -> internal-character tag mappings for review.

Companion to generate_tag_mappings.py (which handles theme tags). Characters are
harder: Danbooru names use surname_given order and series qualifiers
("tomoyo_(cardcaptor_sakura)"), and romanizations differ. So in addition to the
exact normalized match this script:

  - strips Danbooru series qualifiers ("x_(series)" -> "x"),
  - tries a name-order swap (sorted tokens) for surname/given differences,
  - falls back to fuzzy matching (rapidfuzz) for the romanization tail.

Match tiers and the action emitted:
  exact / exact_stripped / swap / swap_stripped  -> action=map   (high confidence)
  ambiguous (one Danbooru name -> several internal tags)
                                                 -> action=review (pick an id)
  fuzzy (>= threshold)                           -> action=review (confirm)
  none                                           -> action=ignore

Output is a DRAFT CSV (default data/character_mappings_draft.csv) with the columns
the importer needs (danbooru_tag, internal_tag_title, internal_tag_id, action)
plus review aids (match_type, score, candidates). Review the `review`/auditable
rows, then merge the good rows into data/tag_mappings.csv and run
import_tag_mappings.py.

Usage:
    uv run python scripts/generate_character_mappings.py \
        [--vocab ml_models/<model>/selected_tags.csv] [--out <draft.csv>] \
        [--fuzzy-threshold 88] [--no-fuzzy]
"""

import argparse
import asyncio
import csv
import re
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from rapidfuzz import fuzz, process
from sqlalchemy import select

from app.core.database import get_async_session
from app.models.character_source_link import CharacterSourceLinks
from app.models.tag import Tags
from scripts.generate_tag_mappings import normalize_tag  # shared theme/char normalizer

CHARACTER_TYPE = 4  # internal Tags.type for characters
CHARACTER_CATEGORY = 4  # model vocab category for characters
DEFAULT_VOCAB = Path("ml_models/caformer_b36.dbv4-full/selected_tags.csv")
DEFAULT_OUTPUT = Path("data/character_mappings_draft.csv")
DEFAULT_FUZZY_THRESHOLD = 88  # rapidfuzz WRatio score required to propose a candidate

_QUALIFIER_RE = re.compile(r"_\([^)]*\)")

OUTPUT_FIELDS = [
    "danbooru_tag",
    "internal_tag_id",
    "internal_tag_title",
    "action",
    "match_type",
    "score",
    "candidates",
]


def strip_qualifier(name: str) -> str:
    """Strip a Danbooru series qualifier: 'x_(series)' -> 'x'."""
    return _QUALIFIER_RE.sub("", name)


def sorted_key(normalized: str) -> str:
    """Order-insensitive key (handles surname/given order swaps)."""
    return " ".join(sorted(normalized.split()))


@dataclass
class InternalIndex:
    norm_to_ids: dict[str, list[int]]
    sorted_to_ids: dict[str, list[int]]
    id_to_title: dict[int, str]
    norm_to_title: dict[str, str]
    norm_choices: list[str]


def build_internal_index(internal: list[tuple[int, str]]) -> InternalIndex:
    """Index internal character tags by normalized title and by sorted-token key."""
    norm_to_ids: dict[str, list[int]] = {}
    sorted_to_ids: dict[str, list[int]] = {}
    id_to_title: dict[int, str] = {}
    norm_to_title: dict[str, str] = {}
    for tag_id, title in internal:
        if not title:
            continue
        id_to_title[tag_id] = title
        n = normalize_tag(title)
        if not n:
            continue
        norm_to_ids.setdefault(n, []).append(tag_id)
        sorted_to_ids.setdefault(sorted_key(n), []).append(tag_id)
        norm_to_title.setdefault(n, title)
    return InternalIndex(
        norm_to_ids=norm_to_ids,
        sorted_to_ids=sorted_to_ids,
        id_to_title=id_to_title,
        norm_to_title=norm_to_title,
        norm_choices=list(norm_to_ids.keys()),
    )


@dataclass
class MatchResult:
    danbooru_tag: str
    internal_tag_id: str
    internal_tag_title: str
    match_type: str  # exact|exact_stripped|swap|swap_stripped|ambiguous|fuzzy|none
    score: int
    candidates: str
    action: str  # map|review|ignore

    def as_row(self) -> dict[str, object]:
        return {
            "danbooru_tag": self.danbooru_tag,
            "internal_tag_id": self.internal_tag_id,
            "internal_tag_title": self.internal_tag_title,
            "action": self.action,
            "match_type": self.match_type,
            "score": self.score,
            "candidates": self.candidates,
        }


def _candidates_str(index: InternalIndex, ids: list[int]) -> str:
    return "; ".join(f"{i}:{index.id_to_title[i]}" for i in ids)


def classify(
    danbooru_name: str,
    index: InternalIndex,
    *,
    fuzzy_threshold: int = DEFAULT_FUZZY_THRESHOLD,
    use_fuzzy: bool = True,
) -> MatchResult:
    """Classify a single Danbooru character name against the internal index."""
    stripped = strip_qualifier(danbooru_name)
    had_qualifier = stripped != danbooru_name
    n = normalize_tag(stripped)

    # 1. exact normalized match
    if n in index.norm_to_ids:
        ids = index.norm_to_ids[n]
        if len(ids) == 1:
            mt = "exact_stripped" if had_qualifier else "exact"
            return MatchResult(danbooru_name, str(ids[0]), index.id_to_title[ids[0]], mt, 100, "", "map")
        return MatchResult(danbooru_name, "", "", "ambiguous", 100, _candidates_str(index, ids), "review")

    # 2. name-order swap (sorted tokens)
    sk = sorted_key(n)
    if sk in index.sorted_to_ids:
        ids = index.sorted_to_ids[sk]
        if len(ids) == 1:
            mt = "swap_stripped" if had_qualifier else "swap"
            return MatchResult(danbooru_name, str(ids[0]), index.id_to_title[ids[0]], mt, 100, "", "map")
        return MatchResult(danbooru_name, "", "", "ambiguous", 100, _candidates_str(index, ids), "review")

    # 3. fuzzy fallback (review only)
    if use_fuzzy and n and index.norm_choices:
        # token_sort_ratio is length-sensitive (unlike WRatio's partial matching, which
        # spuriously scores tiny internal tags like "P"/"Ai" ~90 against long names).
        best = process.extractOne(n, index.norm_choices, scorer=fuzz.token_sort_ratio)
        if best and best[1] >= fuzzy_threshold:
            cand_norm, score, _ = best
            ids = index.norm_to_ids[cand_norm]
            title = index.norm_to_title[cand_norm]
            if len(ids) == 1:
                return MatchResult(danbooru_name, str(ids[0]), title, "fuzzy", round(score), "", "review")
            return MatchResult(danbooru_name, "", title, "fuzzy", round(score), _candidates_str(index, ids), "review")

    return MatchResult(danbooru_name, "", "", "none", 0, "", "ignore")


def match_all(
    danbooru_names: list[str],
    internal: list[tuple[int, str]],
    *,
    fuzzy_threshold: int = DEFAULT_FUZZY_THRESHOLD,
    use_fuzzy: bool = True,
) -> list[MatchResult]:
    """Match every Danbooru character name against the internal character tags."""
    index = build_internal_index(internal)
    return [
        classify(name, index, fuzzy_threshold=fuzzy_threshold, use_fuzzy=use_fuzzy)
        for name in danbooru_names
    ]


def apply_linked_only(
    results: list[MatchResult], linked_ids: set[int]
) -> list[MatchResult]:
    """Restrict auto-`map` to the launchable set: keep `map` only when the internal
    tag is source-linked (clean per analysis) AND the mapping is a clean 1:1 (no
    other Danbooru name maps to the same internal tag). Everything else is demoted
    to `review` with the reason recorded. Non-map results pass through unchanged."""
    map_counts = Counter(r.internal_tag_id for r in results if r.action == "map" and r.internal_tag_id)
    out: list[MatchResult] = []
    for r in results:
        if r.action == "map" and r.internal_tag_id:
            if int(r.internal_tag_id) not in linked_ids:
                r = replace(r, action="review", candidates="needs source link (internal tag unlinked)")
            elif map_counts[r.internal_tag_id] > 1:
                r = replace(
                    r, action="review",
                    candidates="merge collision (multiple Danbooru names -> this tag); needs source-aware",
                )
        out.append(r)
    return out


def load_vocab_characters(vocab_path: Path) -> list[str]:
    """Danbooru character tag names (category 4) from a model's selected_tags.csv."""
    names: list[str] = []
    with open(vocab_path) as f:
        for row in csv.DictReader(f):
            if int(row["category"]) == CHARACTER_CATEGORY:
                names.append(row["name"])
    return names


async def load_internal_characters() -> list[tuple[int, str]]:
    """Internal character tags (type=4) as (tag_id, title)."""
    async with get_async_session() as db:
        rows = (
            await db.execute(
                select(Tags.tag_id, Tags.title).where(Tags.type == CHARACTER_TYPE)  # type: ignore[arg-type]
            )
        ).all()
    return [(tag_id, title) for tag_id, title in rows]


def write_draft(results: list[MatchResult], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    order = {"map": 0, "review": 1, "ignore": 2}
    rows = sorted(results, key=lambda r: (order.get(r.action, 9), r.danbooru_tag))
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(r.as_row() for r in rows)


def summarize(results: list[MatchResult]) -> None:
    by_type: dict[str, int] = {}
    by_action: dict[str, int] = {}
    for r in results:
        by_type[r.match_type] = by_type.get(r.match_type, 0) + 1
        by_action[r.action] = by_action.get(r.action, 0) + 1
    print("\nBy action:", dict(sorted(by_action.items())))
    print("By match_type:", dict(sorted(by_type.items())))
    # Audit: internal tags receiving >1 auto-mapped Danbooru name (series-merge signal).
    per_internal: dict[str, list[str]] = {}
    for r in results:
        if r.action == "map" and r.internal_tag_id:
            per_internal.setdefault(r.internal_tag_id, []).append(r.danbooru_tag)
    merged = {k: v for k, v in per_internal.items() if len(v) > 1}
    print(f"\ninternal tags receiving >1 auto-mapped Danbooru name (audit): {len(merged)}")
    for tid, names in list(merged.items())[:15]:
        print(f"  tag {tid}: {names}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Danbooru->internal character tag mappings (draft).")
    parser.add_argument("--vocab", type=Path, default=DEFAULT_VOCAB, help="model selected_tags.csv")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="output draft CSV")
    parser.add_argument("--fuzzy-threshold", type=int, default=DEFAULT_FUZZY_THRESHOLD)
    parser.add_argument("--no-fuzzy", action="store_true", help="skip the fuzzy fallback")
    parser.add_argument(
        "--linked-only",
        action="store_true",
        help="launchable set: auto-map only characters whose internal tag is source-linked "
        "AND a clean 1:1 match; demote everything else to review",
    )
    args = parser.parse_args()

    if not args.vocab.exists():
        parser.error(f"vocab not found: {args.vocab}")

    out = args.out
    if args.linked_only and out == DEFAULT_OUTPUT:
        out = Path("data/character_mappings_launchable.csv")

    print(f"Loading Danbooru character vocab from {args.vocab} ...")
    danbooru = load_vocab_characters(args.vocab)
    print(f"  {len(danbooru)} character tags")

    print("Loading internal character tags (type=4) ...")
    internal = await load_internal_characters()
    print(f"  {len(internal)} internal character tags")

    print("Matching ...")
    results = match_all(
        danbooru, internal, fuzzy_threshold=args.fuzzy_threshold, use_fuzzy=not args.no_fuzzy
    )

    if args.linked_only:
        async with get_async_session() as db:
            linked_ids = set(
                (await db.execute(select(CharacterSourceLinks.character_tag_id).distinct())).scalars().all()
            )
        results = apply_linked_only(results, linked_ids)
        print(f"linked-only: restricted auto-map to {len(linked_ids)} source-linked internal characters")

    summarize(results)

    write_draft(results, out)
    print(f"\nWrote draft: {out}")
    print("Next: review the action=review rows, merge the good (action=map) rows into")
    print("data/tag_mappings.csv, then run scripts/import_tag_mappings.py.")


if __name__ == "__main__":
    asyncio.run(main())
