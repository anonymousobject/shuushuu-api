# phpBB3 Forum Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import the retired phpBB3 forum (`shuushuuphpbb3`) into the new forum tables as locked, tier-gated threads with correct user attribution and text-link attachments.

**Architecture:** A staged, idempotent management script reads two live source databases (phpBB dump + legacy-site dump), converts s9e-TextFormatter XML post bodies to the site's markdown subset, resolves each poster to a current account via an authoritative `forum_id` map, rehosts attachment files through the existing `R2_ENABLED`-aware storage layer, and inserts categories/threads/posts. One small serialization tweak displays original names for posts attributed to a shared "Archived User" account. API repo only; no frontend changes.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy (async, MariaDB), Alembic, `xml.etree.ElementTree` (stdlib), the app's R2 storage adapter, pytest.

**Spec:** `docs/plans/2026-07-07-forum-import-design.md`

## Global Constraints

- TDD for every task: failing test → minimal code → pass → commit. Run tests with `uv run pytest <path> -v` from the repo root.
- Branch `feat/forum-import` (worktree `.worktrees/feat-forum-import`), stacked on `feat/forum`. NEVER `git add -A`; add explicit paths. Commit messages end with the Co-Authored-By / Claude-Session trailers used in this repo.
- New migration `down_revision` is the forum head **`f565e631d8c2`** (this branch's base). New PK/FK integer columns are `mysql.INTEGER(unsigned=True)`; datetime columns are `DATETIME(6)` in the migration.
- The import reads two source DBs over connections (never parses `.sql`): `--phpbb-url` (default the dev `shuushuuphpbb3`) and `--site-url` (default the dev `php_shuu`). Root creds come from env (`MARIADB_ROOT_PASSWORD`), matching how existing scripts/tests connect.
- Attribution: authoritative `php_shuu.users.forum_id` map, then email match → real current account; everything else → the "Archived User" account. **Every** imported post/thread stores `legacy_poster_id` + `legacy_username`.
- Attachments go through the existing storage layer: R2 when `settings.R2_ENABLED`, local FS otherwise. Dedicated `forum-archive/` key prefix (distinct from board images and `avatars/`).
- Category tiers: Mod Forum → `forum_access_staff` (view+create+reply), Tagging team → `forum_access_tagger`, all other forums public (NULL). All imported threads `locked=true`, `pinned=false`.
- Idempotency via unique `legacy_*` keys — a re-run inserts only what is missing.
- Source charset is `utf8mb3`; target `utf8mb4` (lossless widening).
- Run `uv run ruff check app tests scripts && uv run mypy app` before each commit (touched files clean; pre-existing `tests/conftest.py` ruff findings are not yours).

## File Structure

| File | Responsibility |
|---|---|
| `alembic/versions/<hash>_forum_import_provenance.py` (new) | `legacy_*` columns + indexes; seed "Archived User" |
| `app/models/forum.py` (modify) | add `legacy_*` fields to the 3 models |
| `app/services/forum_import/__init__.py` (new) | package marker |
| `app/services/forum_import/s9e_convert.py` (new) | pure `s9e_to_markdown(xml) -> str` |
| `app/services/forum_import/user_map.py` (new) | `build_user_map(...)` phpBB poster → resolution |
| `app/services/forum_import/attachments.py` (new) | `forum_attachment_url(...)` + `rehost_attachment(...)` |
| `app/core/archived_user.py` (new) | `ARCHIVED_USERNAME` const + cached `get_archived_user_id(db)` |
| `app/api/v1/forum.py` (modify) | `_post_response` shows `legacy_username` for archived-user posts |
| `scripts/import_forum_archive.py` (new) | orchestration; modes `--dry-run` / `--remap` |
| `tests/services/test_s9e_convert.py` (new) | converter unit tests (real samples) |
| `tests/integration/test_forum_import.py` (new) | end-to-end import against restored dev DBs |
| `tests/api/v1/test_forum_archived_user.py` (new) | serialization shows legacy_username |

---

### Task 1: Provenance schema + Archived User

**Files:**
- Create: `alembic/versions/<generated>_forum_import_provenance.py`
- Create: `app/core/archived_user.py`
- Modify: `app/models/forum.py`
- Test: `tests/api/v1/test_forum_import_schema.py`

**Interfaces:**
- Produces: columns `forum_categories.legacy_forum_id`, `forum_threads.legacy_topic_id`, `forum_posts.legacy_post_id`/`legacy_poster_id`/`legacy_username`; `ARCHIVED_USERNAME: str`, `async def get_archived_user_id(db: AsyncSession) -> int | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/v1/test_forum_import_schema.py`:

```python
"""Provenance columns exist and the Archived User is seeded."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.archived_user import ARCHIVED_USERNAME, get_archived_user_id
from app.models.forum import ForumCategories, ForumPosts, ForumThreads
from app.models.user import Users


async def test_legacy_columns_round_trip(db_session: AsyncSession):
    cat = ForumCategories(title="Imp", legacy_forum_id=7)
    db_session.add(cat)
    await db_session.flush()
    thread = ForumThreads(category_id=cat.category_id, title="t", user_id=1, legacy_topic_id=42)
    db_session.add(thread)
    await db_session.flush()
    post = ForumPosts(
        thread_id=thread.thread_id, user_id=1, post_text="hi",
        legacy_post_id=99, legacy_poster_id=1234, legacy_username="OldName",
    )
    db_session.add(post)
    await db_session.commit()
    fetched = await db_session.get(ForumPosts, post.post_id)
    assert fetched.legacy_post_id == 99
    assert fetched.legacy_poster_id == 1234
    assert fetched.legacy_username == "OldName"


async def test_archived_user_seeded(db_session: AsyncSession):
    result = await db_session.execute(select(Users).where(Users.username == ARCHIVED_USERNAME))
    user = result.scalar_one()
    assert user.active == 0
    assert await get_archived_user_id(db_session) == user.id
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/api/v1/test_forum_import_schema.py -v`
Expected: FAIL — `ImportError` on `app.core.archived_user` / unknown `legacy_*` fields.

- [ ] **Step 3: Add model fields**

In `app/models/forum.py`, add to `ForumCategories`:

```python
    legacy_forum_id: int | None = Field(default=None)
```

to `ForumThreads`:

```python
    legacy_topic_id: int | None = Field(default=None)
```

to `ForumPosts`:

```python
    legacy_post_id: int | None = Field(default=None)
    legacy_poster_id: int | None = Field(default=None, index=True)
    legacy_username: str | None = Field(default=None, max_length=255)
```

- [ ] **Step 4: Create the archived-user helper**

Create `app/core/archived_user.py`:

```python
"""The shared 'Archived User' system account used for unmapped phpBB posters."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Users

ARCHIVED_USERNAME = "Archived User"

# Cached across the process; the account never changes once seeded.
_archived_user_id: int | None = None


async def get_archived_user_id(db: AsyncSession) -> int | None:
    """Return the Archived User's user_id (cached), or None if not seeded."""
    global _archived_user_id
    if _archived_user_id is None:
        result = await db.execute(select(Users.user_id).where(Users.username == ARCHIVED_USERNAME))
        _archived_user_id = result.scalar_one_or_none()
    return _archived_user_id
```

- [ ] **Step 5: Author the migration**

Run: `uv run alembic revision -m "forum_import_provenance"`. Replace the generated `upgrade`/`downgrade`:

```python
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers kept from the generated file; down_revision must be:
down_revision: str | Sequence[str] | None = "f565e631d8c2"


def upgrade() -> None:
    op.add_column(
        "forum_categories",
        sa.Column("legacy_forum_id", mysql.INTEGER(unsigned=True), nullable=True),
    )
    op.create_index(
        "uq_forum_categories_legacy_forum_id", "forum_categories", ["legacy_forum_id"], unique=True
    )
    op.add_column(
        "forum_threads",
        sa.Column("legacy_topic_id", mysql.INTEGER(unsigned=True), nullable=True),
    )
    op.create_index(
        "uq_forum_threads_legacy_topic_id", "forum_threads", ["legacy_topic_id"], unique=True
    )
    op.add_column(
        "forum_posts", sa.Column("legacy_post_id", mysql.INTEGER(unsigned=True), nullable=True)
    )
    op.add_column(
        "forum_posts", sa.Column("legacy_poster_id", mysql.INTEGER(unsigned=True), nullable=True)
    )
    op.add_column("forum_posts", sa.Column("legacy_username", sa.String(255), nullable=True))
    op.create_index(
        "uq_forum_posts_legacy_post_id", "forum_posts", ["legacy_post_id"], unique=True
    )
    op.create_index("ix_forum_posts_legacy_poster_id", "forum_posts", ["legacy_poster_id"])

    # Seed the Archived User (idempotent). Direct insert bypasses app validation
    # by design; the account is inactive and cannot log in.
    # gender is NOT NULL without a default; '' is an existing valid value.
    op.execute(
        "INSERT INTO users (username, password, password_type, salt, email, active, admin, "
        "gender, date_joined) "
        "SELECT 'Archived User', '!', 'bcrypt', '!', 'archived@localhost', 0, 0, '', NOW() "
        "FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = 'Archived User')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM users WHERE username = 'Archived User'")
    op.drop_index("ix_forum_posts_legacy_poster_id", table_name="forum_posts")
    op.drop_index("uq_forum_posts_legacy_post_id", table_name="forum_posts")
    op.drop_column("forum_posts", "legacy_username")
    op.drop_column("forum_posts", "legacy_poster_id")
    op.drop_column("forum_posts", "legacy_post_id")
    op.drop_index("uq_forum_threads_legacy_topic_id", table_name="forum_threads")
    op.drop_column("forum_threads", "legacy_topic_id")
    op.drop_index("uq_forum_categories_legacy_forum_id", table_name="forum_categories")
    op.drop_column("forum_categories", "legacy_forum_id")
```

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/api/v1/test_forum_import_schema.py -v`
Expected: PASS (2 tests). The conftest detects the new alembic head and rebuilds the test DB through the migration — if the migration is broken, this is where it surfaces.

Note: `get_archived_user_id` memoizes in a module global. If a later test in the same process seeds a *different* archived user id, that would be stale — acceptable here (the id is stable), but if flakiness appears, reset `app.core.archived_user._archived_user_id = None` in a fixture.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check app tests && uv run mypy app
git add app/models/forum.py app/core/archived_user.py alembic/versions/*forum_import_provenance.py tests/api/v1/test_forum_import_schema.py
git commit -m "feat(forum-import): provenance columns and Archived User account"
```

---

### Task 2: s9e-TextFormatter XML → markdown converter

**Files:**
- Create: `app/services/forum_import/__init__.py` (empty)
- Create: `app/services/forum_import/s9e_convert.py`
- Test: `tests/services/test_s9e_convert.py`

**Interfaces:**
- Produces: `def s9e_to_markdown(xml: str) -> str` — converts a phpBB post `post_text` (s9e `<r>`/`<t>` XML) to the site's markdown subset. Inline `<ATTACHMENT>` elements are dropped (the import appends attachment links separately).

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_s9e_convert.py` (samples are real rows from `shuushuuphpbb3`):

```python
"""s9e XML → markdown, using real phpBB post_text samples."""

from app.services.forum_import.s9e_convert import s9e_to_markdown


def test_plain_text():
    assert s9e_to_markdown("<t>.</t>") == "."


def test_plain_text_with_br():
    assert s9e_to_markdown("<t>line1<br/>line2</t>") == "line1\nline2"


def test_bold():
    xml = "<r><B><s>[b]</s>Thank you, Myu-chan!!!!!   ^_^<e>[/b]</e></B></r>"
    assert s9e_to_markdown(xml) == "**Thank you, Myu-chan!!!!!   ^_^**"


def test_italic():
    assert s9e_to_markdown("<r><I><s>[i]</s>hi<e>[/i]</e></I></r>") == "*hi*"


def test_url_link():
    xml = '<r><URL url="http://www.zerochan.net/937289">http://www.zerochan.net/937289</URL></r>'
    assert s9e_to_markdown(xml) == "[http://www.zerochan.net/937289](http://www.zerochan.net/937289)"


def test_img_becomes_link_to_src():
    xml = (
        '<r><IMG src="http://i.imgur.com/PmlV8Ns.gif"><s>[img]</s>'
        '<URL url="http://i.imgur.com/PmlV8Ns.gif">http://i.imgur.com/PmlV8Ns.gif</URL>'
        "<e>[/img]</e></IMG></r>"
    )
    assert s9e_to_markdown(xml) == "[image](http://i.imgur.com/PmlV8Ns.gif)"


def test_nested_quote():
    xml = (
        '<r><QUOTE author="Fuwari"><s>[quote="Fuwari"]</s>'
        '<QUOTE author="Oni"><s>[quote="Oni"]</s>Does anyone know?<e>[/quote]</e></QUOTE>'
        "Found it.<e>[/quote]</e></QUOTE>Thankies</r>"
    )
    assert s9e_to_markdown(xml) == (
        '[quote="Fuwari"][quote="Oni"]Does anyone know?[/quote]Found it.[/quote]Thankies'
    )


def test_emoji_kept_as_text():
    assert s9e_to_markdown("<r><E>:lol:</E></r>") == ":lol:"


def test_color_stripped_to_text():
    xml = '<r><COLOR color="darkgreen"><s>[color=darkgreen]</s>18<e>[/color]</e></COLOR></r>'
    assert s9e_to_markdown(xml) == "18"


def test_list_becomes_dashes():
    xml = (
        "<r>nominates:\n<LIST><s>[list]</s>\n"
        "<LI><s>[*]</s>Kagemaru</LI>\n<LI><s>[*]</s>Amfest</LI><e>[/list]</e></LIST></r>"
    )
    out = s9e_to_markdown(xml)
    assert "nominates:" in out
    assert "- Kagemaru" in out
    assert "- Amfest" in out


def test_attachment_dropped_inline():
    xml = (
        '<r><ATTACHMENT filename="x.jpg" index="0"><s>[attachment=0]</s>x.jpg'
        "<e>[/attachment]</e></ATTACHMENT></r>"
    )
    assert s9e_to_markdown(xml) == ""


def test_unknown_element_keeps_text():
    # defensive: an unrecognized tag still surfaces its inner text
    assert s9e_to_markdown("<r><WEIRD>kept<br/>text</WEIRD></r>") == "kept\ntext"


def test_malformed_falls_back_to_stripped_text():
    assert s9e_to_markdown("not xml at all") == "not xml at all"


def test_empty():
    assert s9e_to_markdown("") == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/services/test_s9e_convert.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.forum_import.s9e_convert`.

- [ ] **Step 3: Implement the converter**

Create `app/services/forum_import/__init__.py` (empty), then `app/services/forum_import/s9e_convert.py`:

```python
"""Convert phpBB s9e-TextFormatter XML (`post_text`) to the site's markdown subset.

phpBB 3.2+ stores each post as an XML tree: root `<t>` (plain) or `<r>` (rich),
formatted spans as uppercased BBCode-named elements (`<B>`, `<QUOTE author=...>`,
`<URL url=...>`, ...), and `<s>`/`<e>`/`<i>` markers holding the original BBCode
source (which we drop). The mapping targets exactly what parse_markdown renders:
bold/italic, `[quote]`, `[text](url)`. Lossy tags (color/size/font/underline) keep
their inner text. Inline `<ATTACHMENT>` is dropped — the importer appends a
canonical attachment-link list instead.
"""

import re
import xml.etree.ElementTree as ET

# Elements whose entire subtree is the original BBCode source markup: drop them.
_DROP_TAGS = {"s", "e", "i"}


def s9e_to_markdown(xml: str) -> str:
    if not xml:
        return ""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        # Defensive: not valid s9e XML — strip any tags and return the text.
        return re.sub(r"<[^>]+>", "", xml).strip()
    return _normalize(_render(root))


def _inner(el: ET.Element) -> str:
    parts = [el.text or ""]
    for child in el:
        parts.append(_render(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _render(el: ET.Element) -> str:
    tag = el.tag
    if tag in _DROP_TAGS:
        return ""
    if tag == "br":
        return "\n"
    if tag == "IMG":
        return f"[image]({el.get('src', '')})"
    if tag == "ATTACHMENT":
        return ""
    inner = _inner(el)
    if tag in ("r", "t", "E", "COLOR", "SIZE", "FONT", "U", "CODE", "LIST"):
        return inner
    if tag == "B":
        return f"**{inner}**"
    if tag == "I":
        return f"*{inner}*"
    if tag == "QUOTE":
        author = el.get("author")
        return f'[quote="{author}"]{inner}[/quote]' if author else f"[quote]{inner}[/quote]"
    if tag == "URL":
        url = el.get("url", "")
        return f"[{inner or url}]({url})"
    if tag == "LI":
        return f"\n- {inner.strip()}"
    # Unknown element: keep its inner text (nothing silently dropped).
    return inner


def _normalize(text: str) -> str:
    # Collapse 3+ blank lines the list/quote handling can introduce.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/services/test_s9e_convert.py -v`
Expected: PASS (15 tests).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app tests && uv run mypy app
git add app/services/forum_import/__init__.py app/services/forum_import/s9e_convert.py tests/services/test_s9e_convert.py
git commit -m "feat(forum-import): s9e XML to markdown converter"
```

---

### Task 3: Poster resolution (pure function)

**Files:**
- Create: `app/services/forum_import/user_map.py`
- Test: `tests/services/test_user_map.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) PosterResolution(site_user_id: int | None, legacy_username: str)` and `def resolve_posters(posters, forum_id_map, target_user_ids, target_email_to_id) -> dict[int, PosterResolution]`. `site_user_id is None` means "attribute to Archived User". The script does the DB I/O and passes plain dicts/sets, so this core is pure and unit-testable.

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_user_map.py`:

```python
from app.services.forum_import.user_map import PosterResolution, resolve_posters


def test_forum_id_wins_and_is_rename_safe():
    # phpbb 100 links to site user 500 via forum_id, even though its phpbb name
    # ("OldName") differs from whoever holds it now — id map is authoritative.
    out = resolve_posters(
        posters={100: ("OldName", "a@x.com")},
        forum_id_map={100: 500},
        target_user_ids={500},
        target_email_to_id={"a@x.com": 999},
    )
    assert out[100] == PosterResolution(site_user_id=500, legacy_username="OldName")


def test_email_fallback_when_no_forum_id():
    out = resolve_posters(
        posters={101: ("Bob", "bob@x.com")},
        forum_id_map={},
        target_user_ids={42},
        target_email_to_id={"bob@x.com": 42},
    )
    assert out[101] == PosterResolution(site_user_id=42, legacy_username="Bob")


def test_forum_id_target_missing_falls_through_to_archived():
    # forum_id points at a user_id that doesn't exist in the target (e.g. dev
    # subset) and no email match → Archived User.
    out = resolve_posters(
        posters={102: ("Ghost", "")},
        forum_id_map={102: 700},
        target_user_ids=set(),
        target_email_to_id={},
    )
    assert out[102] == PosterResolution(site_user_id=None, legacy_username="Ghost")


def test_username_only_is_not_trusted():
    # A username-only match is deliberately NOT resolved to a real account.
    out = resolve_posters(
        posters={103: ("SharedName", "")},
        forum_id_map={},
        target_user_ids={1},
        target_email_to_id={},
    )
    assert out[103].site_user_id is None
    assert out[103].legacy_username == "SharedName"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/services/test_user_map.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `app/services/forum_import/user_map.py`:

```python
"""Resolve phpBB posters to current site accounts.

Trust order (identity-safe): authoritative forum_id map → email match → Archived
User. Username-only matches are intentionally not trusted (a freed username may
belong to a different person now). Pure function; the caller supplies data.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PosterResolution:
    site_user_id: int | None  # None → attribute to the Archived User account
    legacy_username: str


def resolve_posters(
    posters: dict[int, tuple[str, str]],  # phpbb_id -> (username, lower_email)
    forum_id_map: dict[int, int],  # phpbb_id -> legacy/site user_id
    target_user_ids: set[int],  # user_ids that exist in the target site
    target_email_to_id: dict[str, int],  # lower(email) -> target user_id
) -> dict[int, PosterResolution]:
    out: dict[int, PosterResolution] = {}
    for phpbb_id, (username, email) in posters.items():
        site_id: int | None = None
        mapped = forum_id_map.get(phpbb_id)
        if mapped is not None and mapped in target_user_ids:
            site_id = mapped
        elif email and email in target_email_to_id:
            site_id = target_email_to_id[email]
        out[phpbb_id] = PosterResolution(site_user_id=site_id, legacy_username=username)
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/services/test_user_map.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app tests && uv run mypy app
git add app/services/forum_import/user_map.py tests/services/test_user_map.py
git commit -m "feat(forum-import): identity-safe poster resolution"
```

---

### Task 4: Attachment rehosting + URL

**Files:**
- Create: `app/services/forum_import/attachments.py`
- Test: `tests/services/test_forum_attachments.py`

**Interfaces:**
- Produces: `def forum_attachment_url(physical_filename: str) -> str` (env-aware) and `async def rehost_attachment(physical_filename: str, source_path: Path, content_type: str) -> None` (R2 when `settings.R2_ENABLED`, local copy otherwise). Both use the dedicated `forum-archive/` prefix.

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_forum_attachments.py`:

```python
from pathlib import Path

import pytest

from app.config import settings
from app.services.forum_import.attachments import forum_attachment_url, rehost_attachment


def test_url_local_when_r2_disabled(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://dev.local")
    assert forum_attachment_url("abc123") == "http://dev.local/images/forum-archive/abc123"


def test_url_cdn_when_r2_enabled(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.example")
    assert forum_attachment_url("abc123") == "https://cdn.example/forum-archive/abc123"


async def test_rehost_local_copies_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello")
    await rehost_attachment("phys_key", src, "image/jpeg")
    dest = tmp_path / "forum-archive" / "phys_key"
    assert dest.read_bytes() == b"hello"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/services/test_forum_attachments.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `app/services/forum_import/attachments.py`:

```python
"""Rehost phpBB attachment files through the app's storage layer and build URLs.

Mirrors the avatar pattern (app/services/avatar.py): R2 CDN URL when
R2_ENABLED, local media URL otherwise — under a dedicated `forum-archive/` key
prefix distinct from board images and `avatars/`.
"""

import shutil
from pathlib import Path

from app.config import settings
from app.core.r2_client import get_r2_storage

_PREFIX = "forum-archive"


def forum_attachment_url(physical_filename: str) -> str:
    if settings.R2_ENABLED:
        return f"{settings.R2_PUBLIC_CDN_URL}/{_PREFIX}/{physical_filename}"
    return f"{settings.IMAGE_BASE_URL}/images/{_PREFIX}/{physical_filename}"


async def rehost_attachment(physical_filename: str, source_path: Path, content_type: str) -> None:
    """Store one attachment file. Idempotent-friendly (overwrite is harmless)."""
    if settings.R2_ENABLED:
        body = source_path.read_bytes()
        await get_r2_storage().upload_bytes(
            bucket=settings.R2_PUBLIC_BUCKET,
            key=f"{_PREFIX}/{physical_filename}",
            body=body,
            content_type=content_type,
        )
    else:
        dest = Path(settings.STORAGE_PATH) / _PREFIX / physical_filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dest)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/services/test_forum_attachments.py -v`
Expected: PASS (3 tests). The R2 path is exercised on the test/prod environments, not dev.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app tests && uv run mypy app
git add app/services/forum_import/attachments.py tests/services/test_forum_attachments.py
git commit -m "feat(forum-import): env-aware attachment rehosting and URLs"
```

---

### Task 5: Import orchestration script

**Files:**
- Create: `scripts/import_forum_archive.py`
- Test: `tests/integration/test_forum_import.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4, plus `ForumCategories`/`ForumThreads`/`ForumPosts` and `get_archived_user_id`.
- Produces: `async def run_import(target, phpbb_url, legacy_url, backup_files_dir, *, only_forum_ids=None, dry_run=False, remap_only=False) -> ImportStats`; `main()` (argv → run_import against the app DB). `ImportStats` is a dataclass with `categories`, `threads`, `posts`, `resolved`, `archived` int counts.
- Tier map: `{7: "forum_access_staff", 10: "forum_access_tagger"}` by phpBB `forum_id`; all other type-1 forums public.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_forum_import.py` (runs against the real restored dev DBs; imports one small forum to stay fast):

```python
"""End-to-end import of one phpBB forum into the forum tables (real source DBs)."""

import os
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.archived_user import get_archived_user_id
from app.models.forum import ForumCategories, ForumPosts, ForumThreads

pytestmark = pytest.mark.forum_import

ROOT = os.environ.get("MARIADB_ROOT_PASSWORD", "root_password")
PHPBB_URL = f"mysql+aiomysql://root:{ROOT}@localhost:3306/shuushuuphpbb3"
LEGACY_URL = f"mysql+aiomysql://root:{ROOT}@localhost:3306/php_shuu"
BACKUP = Path("/sakura/backups/forums-2026-02-20/files")
GAMING_FORUM_ID = 16  # small: 3 topics / 15 posts


def _sources_available() -> bool:
    return BACKUP.exists()


@pytest.mark.skipif(not _sources_available(), reason="phpBB backup files not present")
async def test_import_one_forum(db_session: AsyncSession, monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))  # attachments → tmp
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    from scripts.import_forum_archive import run_import

    stats = await run_import(
        db_session, PHPBB_URL, LEGACY_URL, BACKUP, only_forum_ids={GAMING_FORUM_ID}
    )
    assert stats.categories == 1
    assert stats.threads == 3
    assert stats.posts == 15

    cat = (
        await db_session.execute(
            select(ForumCategories).where(ForumCategories.legacy_forum_id == GAMING_FORUM_ID)
        )
    ).scalar_one()
    assert cat.title == "Gaming"
    assert cat.view_perm is None  # public forum

    threads = (
        await db_session.execute(
            select(ForumThreads).where(ForumThreads.category_id == cat.category_id)
        )
    ).scalars().all()
    assert len(threads) == 3
    assert all(t.locked for t in threads)
    assert all(not t.pinned for t in threads)
    # denorm set
    for t in threads:
        assert t.post_count > 0
        assert t.last_post_at is not None

    # every post carries provenance
    posts = (await db_session.execute(select(ForumPosts))).scalars().all()
    assert all(p.legacy_post_id is not None for p in posts)
    assert all(p.legacy_poster_id is not None for p in posts)
    assert all(p.legacy_username for p in posts)
    # archived-user posts (if any) have a legacy name preserved
    archived_id = await get_archived_user_id(db_session)
    for p in posts:
        if p.user_id == archived_id:
            assert p.legacy_username


@pytest.mark.skipif(not _sources_available(), reason="phpBB backup files not present")
async def test_import_is_idempotent(db_session: AsyncSession, monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    from scripts.import_forum_archive import run_import

    await run_import(db_session, PHPBB_URL, LEGACY_URL, BACKUP, only_forum_ids={GAMING_FORUM_ID})
    total1 = (await db_session.execute(select(func.count()).select_from(ForumPosts))).scalar()
    await run_import(db_session, PHPBB_URL, LEGACY_URL, BACKUP, only_forum_ids={GAMING_FORUM_ID})
    total2 = (await db_session.execute(select(func.count()).select_from(ForumPosts))).scalar()
    assert total1 == total2  # re-run created nothing new
```

Register the marker: in `tests/conftest.py`'s `pytest_configure`, add:

```python
    config.addinivalue_line("markers", "forum_import: import tests needing the restored phpBB DBs")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/integration/test_forum_import.py -v`
Expected: FAIL — `ModuleNotFoundError: scripts.import_forum_archive` (or skip if the backup dir is absent — it is present on dev).

- [ ] **Step 3: Implement the script**

Create `scripts/import_forum_archive.py`:

```python
"""Import the retired phpBB3 forum into the new forum tables.

Reads two live source databases (never .sql): the phpBB dump and the legacy
php_shuu dump. Converts s9e post XML to markdown, resolves posters to current
accounts, rehosts attachments, and inserts categories/threads/posts as locked,
tier-gated threads. Idempotent via legacy_* unique keys.

Usage:
    uv run python -m scripts.import_forum_archive [--dry-run] [--remap] \
        [--phpbb-url URL] [--site-url URL]
"""

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.core.archived_user import get_archived_user_id
from app.core.database import AsyncSessionLocal
from app.models.forum import ForumCategories, ForumPosts, ForumThreads
from app.services.forum_import.attachments import forum_attachment_url, rehost_attachment
from app.services.forum_import.s9e_convert import s9e_to_markdown
from app.services.forum_import.user_map import PosterResolution, resolve_posters

DEFAULT_PHPBB_URL = "mysql+aiomysql://root:{pw}@localhost:3306/shuushuuphpbb3"
DEFAULT_SITE_URL = "mysql+aiomysql://root:{pw}@localhost:3306/php_shuu"
BACKUP_FILES_DIR = Path("/sakura/backups/forums-2026-02-20/files")

# phpBB forum_id → access-tier permission (others public/NULL)
TIER_BY_FORUM_ID = {7: "forum_access_staff", 10: "forum_access_tagger"}


@dataclass
class ImportStats:
    categories: int = 0
    threads: int = 0
    posts: int = 0
    resolved: int = 0
    archived: int = 0
    attachments: int = 0
    skipped_existing: int = 0
    remapped: int = 0
    notes: list[str] = field(default_factory=list)


async def _build_resolution(
    target: AsyncSession, phpbb_url: str, legacy_url: str
) -> dict[int, PosterResolution]:
    phpbb = create_async_engine(phpbb_url)
    legacy = create_async_engine(legacy_url)
    try:
        async with phpbb.connect() as pc:
            posters = {
                int(r[0]): (r[1], (r[2] or "").lower().strip())
                for r in await pc.execute(
                    text(
                        "SELECT u.user_id, u.username, u.user_email FROM phpbb_users u "
                        "JOIN (SELECT DISTINCT poster_id FROM phpbb_posts WHERE poster_id>1) p "
                        "ON p.poster_id=u.user_id"
                    )
                )
            }
        async with legacy.connect() as lc:
            forum_id_map = {
                int(r[0]): int(r[1])
                for r in await lc.execute(
                    text("SELECT forum_id, user_id FROM users WHERE forum_id IS NOT NULL AND forum_id>0")
                )
            }
    finally:
        await phpbb.dispose()
        await legacy.dispose()

    rows = await target.execute(text("SELECT user_id, LOWER(email) FROM users"))
    target_user_ids: set[int] = set()
    target_email_to_id: dict[str, int] = {}
    for uid, email in rows:
        target_user_ids.add(int(uid))
        if email:
            target_email_to_id[email.strip()] = int(uid)
    return resolve_posters(posters, forum_id_map, target_user_ids, target_email_to_id)


async def _post_attachment_links(pc, post_id: int, backup_dir: Path, stats: ImportStats) -> str:
    rows = list(
        await pc.execute(
            text(
                "SELECT physical_filename, real_filename, mimetype FROM phpbb_attachments "
                "WHERE post_msg_id=:pid AND is_orphan=0 AND in_message=0 ORDER BY attach_id"
            ),
            {"pid": post_id},
        )
    )
    lines = []
    for physical, real, mime in rows:
        src = backup_dir / physical
        if src.exists():
            await rehost_attachment(physical, src, mime or "application/octet-stream")
            lines.append(f"\U0001F4CE [{real}]({forum_attachment_url(physical)})")
            stats.attachments += 1
        else:
            stats.notes.append(f"missing attachment file {physical} (post {post_id})")
    return ("\n\n" + "\n".join(lines)) if lines else ""


async def run_import(
    target: AsyncSession,
    phpbb_url: str,
    legacy_url: str,
    backup_files_dir: Path,
    *,
    only_forum_ids: set[int] | None = None,
    dry_run: bool = False,
    remap_only: bool = False,
) -> ImportStats:
    stats = ImportStats()
    resolution = await _build_resolution(target, phpbb_url, legacy_url)
    archived_id = await get_archived_user_id(target)
    if archived_id is None:
        raise RuntimeError("Archived User not seeded — run the migration first.")

    def author_of(poster_id: int) -> tuple[int, str]:
        res = resolution.get(poster_id)
        if res is None:  # e.g. guest (poster_id=1) — attribute to Archived
            return archived_id, "Guest"
        if res.site_user_id is None:
            return archived_id, res.legacy_username
        return res.site_user_id, res.legacy_username

    if remap_only:
        for phpbb_id, res in resolution.items():
            uid = res.site_user_id if res.site_user_id is not None else archived_id
            r = await target.execute(
                text("UPDATE forum_posts SET user_id=:u WHERE legacy_poster_id=:p"),
                {"u": uid, "p": phpbb_id},
            )
            stats.remapped += r.rowcount or 0
            await target.execute(
                text("UPDATE forum_threads SET user_id=:u WHERE legacy_topic_id IN "
                     "(SELECT DISTINCT thread_id FROM forum_posts WHERE legacy_poster_id=:p) "
                     "AND user_id<>:u"),
                {"u": uid, "p": phpbb_id},
            )
        if not dry_run:
            await target.commit()
        return stats

    phpbb = create_async_engine(phpbb_url)
    try:
        async with phpbb.connect() as pc:
            forums = list(
                await pc.execute(
                    text(
                        "SELECT forum_id, forum_name, forum_desc, left_id FROM phpbb_forums "
                        "WHERE forum_type=1 ORDER BY left_id"
                    )
                )
            )
            for fid, fname, fdesc, left_id in forums:
                if only_forum_ids is not None and fid not in only_forum_ids:
                    continue
                cat = await _upsert_category(target, fid, fname, fdesc, left_id, stats, dry_run)
                topics = list(
                    await pc.execute(
                        text(
                            "SELECT topic_id, topic_title, topic_time FROM phpbb_topics "
                            "WHERE forum_id=:fid AND topic_visibility=1 ORDER BY topic_time"
                        ),
                        {"fid": fid},
                    )
                )
                for tid, title, ttime in topics:
                    await _import_topic(
                        target, pc, cat, tid, title, ttime, author_of,
                        backup_files_dir, stats, dry_run,
                    )
    finally:
        await phpbb.dispose()

    if dry_run:
        await target.rollback()

    for res in resolution.values():
        if res.site_user_id is None:
            stats.archived += 1
        else:
            stats.resolved += 1
    return stats


async def _upsert_category(target, fid, fname, fdesc, left_id, stats, dry_run):
    existing = (
        await target.execute(
            select(ForumCategories).where(ForumCategories.legacy_forum_id == fid)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    tier = TIER_BY_FORUM_ID.get(fid)
    cat = ForumCategories(
        title=fname,
        description=fdesc or None,
        sort_order=int(left_id),
        legacy_forum_id=fid,
        view_perm=tier,
        thread_create_perm=tier,
        reply_perm=tier,
    )
    target.add(cat)
    await target.flush()
    stats.categories += 1
    return cat


async def _import_topic(target, pc, cat, tid, title, ttime, author_of, backup_dir, stats, dry_run):
    if (
        await target.execute(
            select(ForumThreads.thread_id).where(ForumThreads.legacy_topic_id == tid)
        )
    ).scalar_one_or_none() is not None:
        stats.skipped_existing += 1
        return
    posts = list(
        await pc.execute(
            text(
                "SELECT post_id, poster_id, post_time, post_text FROM phpbb_posts "
                "WHERE topic_id=:tid AND post_visibility=1 ORDER BY post_time, post_id"
            ),
            {"tid": tid},
        )
    )
    if not posts:
        return
    first_uid, _ = author_of(int(posts[0][1]))
    thread = ForumThreads(
        category_id=cat.category_id,
        title=title[:255],
        user_id=first_uid,
        date=datetime.fromtimestamp(int(ttime), UTC),
        locked=True,
        pinned=False,
        legacy_topic_id=int(tid),
    )
    target.add(thread)
    await target.flush()
    stats.threads += 1

    last_at = None
    last_uid = first_uid
    for post_id, poster_id, ptime, ptext in posts:
        uid, legacy_name = author_of(int(poster_id))
        body = s9e_to_markdown(ptext or "")
        body += await _post_attachment_links(pc, int(post_id), backup_dir, stats)
        pdate = datetime.fromtimestamp(int(ptime), UTC)
        target.add(
            ForumPosts(
                thread_id=thread.thread_id,
                user_id=uid,
                post_text=body.strip() or "(empty)",
                date=pdate,
                legacy_post_id=int(post_id),
                legacy_poster_id=int(poster_id),
                legacy_username=legacy_name,
            )
        )
        stats.posts += 1
        last_at, last_uid = pdate, uid
    await target.flush()
    thread.post_count = len(posts)
    thread.last_post_at = last_at
    thread.last_post_user_id = last_uid
    # Real runs commit per topic (idempotent + resumable). Dry runs stay in one
    # transaction and are rolled back wholesale at the end of run_import.
    if not dry_run:
        await target.commit()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remap", action="store_true")
    ap.add_argument("--phpbb-url", default=None)
    ap.add_argument("--site-url", default=None)
    args = ap.parse_args()
    import os

    pw = os.environ.get("MARIADB_ROOT_PASSWORD", "root_password")
    phpbb_url = args.phpbb_url or DEFAULT_PHPBB_URL.format(pw=pw)
    legacy_url = args.site_url or DEFAULT_SITE_URL.format(pw=pw)

    async with AsyncSessionLocal() as session:
        stats = await run_import(
            session, phpbb_url, legacy_url, BACKUP_FILES_DIR,
            dry_run=args.dry_run, remap_only=args.remap,
        )
    print(stats)


if __name__ == "__main__":
    asyncio.run(main())
```

Note: `AsyncSessionLocal` is the session factory in `app/core/database.py` (verified). The integration test injects `db_session` directly and does not exercise `main()`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/integration/test_forum_import.py -v`
Expected: PASS (2 tests) — imports the Gaming forum (1 category / 3 threads / 15 posts), all threads locked, provenance populated, idempotent re-run adds nothing.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app tests scripts && uv run mypy app scripts
git add scripts/import_forum_archive.py tests/integration/test_forum_import.py tests/conftest.py
git commit -m "feat(forum-import): import orchestration script"
```

---

### Task 6: Show original names for Archived-User posts

**Files:**
- Modify: `app/api/v1/forum.py` (`_post_response` + `get_thread`)
- Test: `tests/api/v1/test_forum_archived_user.py`

**Interfaces:**
- Consumes: `get_archived_user_id`, `legacy_username`.
- Produces: `_post_response(post, user, is_moderator, archived_user_id=None)` — when `archived_user_id` is set and `post.user_id == archived_user_id` and `post.legacy_username`, the returned `UserSummary.username` is `legacy_username`. Only `get_thread` passes `archived_user_id`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/v1/test_forum_archived_user.py`:

```python
"""Imported posts attributed to the Archived User display the original name."""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.archived_user import ARCHIVED_USERNAME, get_archived_user_id
from app.models.forum import ForumCategories, ForumPosts, ForumThreads
from app.models.user import Users


async def test_archived_post_shows_legacy_username(client: AsyncClient, db_session: AsyncSession):
    # Archived User is seeded by the migration.
    archived_id = await get_archived_user_id(db_session)
    assert archived_id is not None

    cat = ForumCategories(title="Imported")
    db_session.add(cat)
    await db_session.flush()
    thread = ForumThreads(category_id=cat.category_id, title="Old thread", user_id=archived_id,
                          locked=True, legacy_topic_id=1)
    db_session.add(thread)
    await db_session.flush()
    db_session.add(ForumPosts(
        thread_id=thread.thread_id, user_id=archived_id, post_text="old body",
        legacy_post_id=1, legacy_poster_id=555, legacy_username="RetroPoster",
    ))
    await db_session.commit()

    resp = await client.get(f"/api/v1/forum/threads/{thread.thread_id}")
    assert resp.status_code == 200
    post = resp.json()["posts"][0]
    assert post["user"]["username"] == "RetroPoster"  # not "Archived User"
    assert post["user_id"] == archived_id
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/api/v1/test_forum_archived_user.py -v`
Expected: FAIL — `post["user"]["username"]` is `"Archived User"`, not `"RetroPoster"`.

- [ ] **Step 3: Implement**

In `app/api/v1/forum.py`, update `_post_response` signature and body:

```python
def _post_response(
    post: ForumPosts, user: UserSummary, is_moderator: bool, archived_user_id: int | None = None
) -> ForumPostResponse:
    """Build a post response; tombstoned posts have their text blanked for
    callers without FORUM_MODERATE. Imported posts attributed to the Archived
    User display their original (legacy) poster name."""
    if archived_user_id is not None and post.user_id == archived_user_id and post.legacy_username:
        user = user.model_copy(update={"username": post.legacy_username})
    return ForumPostResponse(
        post_id=post.post_id or 0,
        thread_id=post.thread_id,
        user_id=post.user_id,
        post_text="" if post.deleted and not is_moderator else post.post_text,
        date=post.date,
        deleted=post.deleted,
        update_count=post.update_count,
        last_updated=post.last_updated,
        last_updated_user_id=post.last_updated_user_id,
        user=user,
    )
```

Add the import at the top of `app/api/v1/forum.py`:

```python
from app.core.archived_user import get_archived_user_id
```

In `get_thread`, resolve the archived id once and pass it into every `_post_response`. Find the posts list-comprehension near the end of `get_thread` and change it to:

```python
    archived_user_id = await get_archived_user_id(db)
    return ForumThreadDetailResponse(
        thread=_thread_summary(thread, summaries, unread=False),
        can_reply=current_user is not None and can_access(perms, category.reply_perm),
        can_moderate=is_moderator,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        posts=[
            _post_response(p, summaries[p.user_id], is_moderator, archived_user_id)
            for p in posts
        ],
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/api/v1/test_forum_archived_user.py tests/api/v1/test_forum_threads.py -v`
Expected: PASS (the new test plus the existing thread suite — the extra optional arg defaults to None everywhere else, so no regression).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app tests && uv run mypy app
git add app/api/v1/forum.py tests/api/v1/test_forum_archived_user.py
git commit -m "feat(forum-import): display original names for archived-user posts"
```

---

### Task 7: Full-suite verification + dev import run

**Files:** none new.

- [ ] **Step 1: Full test suite + lint/types**

Run: `uv run pytest -q`
Expected: PASS with no new failures (the two `forum_import` integration tests run against the restored dev DBs). Then `uv run ruff check app tests scripts && uv run mypy app scripts` — clean (pre-existing `tests/conftest.py` ruff findings excepted).

- [ ] **Step 2: Apply the migration to the dev database**

Run: `uv run alembic upgrade head`
Then confirm the Archived User exists and the columns are present:

```bash
uv run python -c "
import asyncio
from sqlalchemy import text
from app.core.database import engine
async def main():
    async with engine.connect() as c:
        print('archived:', (await c.execute(text(\"SELECT user_id FROM users WHERE username='Archived User'\"))).scalar())
asyncio.run(main())
"
```

- [ ] **Step 3: Full dev import (all 9 forums)**

Run: `uv run python -m scripts.import_forum_archive`
Expected printed `ImportStats`: `categories=9`, `threads=61`, `posts=14704` (± any all-empty topics), `resolved` ≈ 408, `archived` ≈ 92, `attachments=357`, `skipped_existing=0`, empty `notes` (no missing attachment files). Attachments land under `${STORAGE_PATH}/forum-archive/` (dev is local-FS).

- [ ] **Step 4: Verify in the running dev forum UI**

With the dev API + frontend up, browse `/forum`:
- All 9 categories present; **Mod Forum** and **Tagging team** are hidden from a non-staff account (staff/tagger tiers) and visible to an admin.
- Open a General thread: posts render (bold/quote/links converted), attachment posts show `📎 filename` links resolving under `/images/forum-archive/...`, all imported threads show the lock icon and no reply box.
- Author names: real accounts link to profiles; unmapped posters show their original name (Archived-User attribution).

- [ ] **Step 5: Verify idempotency + remap on dev**

Run: `uv run python -m scripts.import_forum_archive` again → `ImportStats` shows `skipped_existing=61`, `threads=0`, `posts=0` (nothing re-created).
Run: `uv run python -m scripts.import_forum_archive --remap` → completes; `remapped` reflects rows re-pointed (0 on a stable map).

- [ ] **Step 6: Hand-off note**

The dev import writes local-FS attachment URLs. The R2 upload path (Task 4) is first exercised on the **test/shuu** environment (`R2_ENABLED=true`, its own bucket), then prod — per the spec's rollout. No code change between environments; only `--phpbb-url`/`--site-url` and the env's storage config differ.
