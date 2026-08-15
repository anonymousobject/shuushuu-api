# Tag Alias/Parent Validation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent alias tags from being used as parent tags, and prevent parent tags from being made into aliases.

**Architecture:** Add a shared `validate_tag_relationships()` function in `app/api/v1/tags.py` that both `create_tag` and `update_tag` call. This consolidates existing existence checks and adds the two new constraint checks. A one-off audit script reports existing violations.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async, pytest

---

### Task 1: Write failing tests for "cannot use alias tag as parent" on create

**Files:**
- Modify: `tests/api/v1/test_tags.py`

**Step 1: Write the failing test**

Add to `TestCreateTag` class:

```python
async def test_create_tag_rejects_alias_as_parent(
    self, client: AsyncClient, db_session: AsyncSession
):
    """Test that creating a tag with an alias tag as parent is rejected."""
    # Create TAG_CREATE permission
    perm = Perms(title="tag_create", desc="Create tags")
    db_session.add(perm)
    await db_session.commit()
    await db_session.refresh(perm)

    # Create admin user
    admin = Users(
        username="admin_alias_parent",
        password=get_password_hash("AdminPassword123!"),
        password_type="bcrypt",
        salt="",
        email="admin_alias_parent@example.com",
        active=1,
        admin=1,
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)

    # Grant TAG_CREATE permission
    user_perm = UserPerms(
        user_id=admin.user_id,
        perm_id=perm.perm_id,
        permvalue=1,
    )
    db_session.add(user_perm)

    # Create canonical tag and alias tag
    canonical = Tags(title="swimsuit", desc="", type=TagType.THEME)
    db_session.add(canonical)
    await db_session.commit()
    await db_session.refresh(canonical)

    alias = Tags(title="bathing suit", desc="", type=TagType.THEME, alias_of=canonical.tag_id)
    db_session.add(alias)
    await db_session.commit()
    await db_session.refresh(alias)

    # Login
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin_alias_parent", "password": "AdminPassword123!"},
    )
    access_token = login_response.json()["access_token"]

    # Try to create a tag with the alias as parent
    response = await client.post(
        "/api/v1/tags",
        json={
            "title": "bikini",
            "type": TagType.THEME,
            "inheritedfrom_id": alias.tag_id,
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "alias" in detail.lower()
    assert str(canonical.tag_id) in detail
    assert "swimsuit" in detail
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_tags.py::TestCreateTag::test_create_tag_rejects_alias_as_parent -v`
Expected: FAIL — the endpoint currently allows alias tags as parents.

---

### Task 2: Write failing tests for "cannot use alias tag as parent" on update

**Files:**
- Modify: `tests/api/v1/test_tags.py`

**Step 1: Write the failing test**

Add to `TestUpdateTag` class:

```python
async def test_update_tag_rejects_alias_as_parent(
    self, client: AsyncClient, db_session: AsyncSession
):
    """Test that setting an alias tag as parent via update is rejected."""
    # Create TAG_UPDATE permission
    perm = Perms(title="tag_update", desc="Update tags")
    db_session.add(perm)
    await db_session.commit()
    await db_session.refresh(perm)

    # Create admin user
    admin = Users(
        username="admin_alias_parent_upd",
        password=get_password_hash("AdminPassword123!"),
        password_type="bcrypt",
        salt="",
        email="admin_alias_parent_upd@example.com",
        active=1,
        admin=1,
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)

    # Grant TAG_UPDATE permission
    user_perm = UserPerms(
        user_id=admin.user_id,
        perm_id=perm.perm_id,
        permvalue=1,
    )
    db_session.add(user_perm)

    # Create canonical tag, alias tag, and a child tag
    canonical = Tags(title="swimsuit upd", desc="", type=TagType.THEME)
    db_session.add(canonical)
    await db_session.commit()
    await db_session.refresh(canonical)

    alias = Tags(title="bathing suit upd", desc="", type=TagType.THEME, alias_of=canonical.tag_id)
    child = Tags(title="bikini upd", desc="", type=TagType.THEME)
    db_session.add_all([alias, child])
    await db_session.commit()
    await db_session.refresh(alias)
    await db_session.refresh(child)

    # Login
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin_alias_parent_upd", "password": "AdminPassword123!"},
    )
    access_token = login_response.json()["access_token"]

    # Try to set the alias as parent of the child tag
    response = await client.put(
        f"/api/v1/tags/{child.tag_id}",
        json={
            "title": "bikini upd",
            "type": TagType.THEME,
            "inheritedfrom_id": alias.tag_id,
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "alias" in detail.lower()
    assert str(canonical.tag_id) in detail
    assert "swimsuit upd" in detail
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_tags.py::TestUpdateTag::test_update_tag_rejects_alias_as_parent -v`
Expected: FAIL

---

### Task 3: Write failing tests for "cannot make a parent tag into an alias"

**Files:**
- Modify: `tests/api/v1/test_tags.py`

**Step 1: Write the failing test**

Add to `TestUpdateTag` class:

```python
async def test_update_tag_rejects_aliasing_parent_with_children(
    self, client: AsyncClient, db_session: AsyncSession
):
    """Test that making a tag with children into an alias is rejected."""
    # Create TAG_UPDATE permission
    perm = Perms(title="tag_update", desc="Update tags")
    db_session.add(perm)
    await db_session.commit()
    await db_session.refresh(perm)

    # Create admin user
    admin = Users(
        username="admin_parent_alias",
        password=get_password_hash("AdminPassword123!"),
        password_type="bcrypt",
        salt="",
        email="admin_parent_alias@example.com",
        active=1,
        admin=1,
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)

    # Grant TAG_UPDATE permission
    user_perm = UserPerms(
        user_id=admin.user_id,
        perm_id=perm.perm_id,
        permvalue=1,
    )
    db_session.add(user_perm)

    # Create parent tag and child tags
    parent = Tags(title="swimwear", desc="", type=TagType.THEME)
    db_session.add(parent)
    await db_session.commit()
    await db_session.refresh(parent)

    child1 = Tags(title="bikini child", desc="", type=TagType.THEME, inheritedfrom_id=parent.tag_id)
    child2 = Tags(title="one-piece", desc="", type=TagType.THEME, inheritedfrom_id=parent.tag_id)
    db_session.add_all([child1, child2])
    await db_session.commit()
    await db_session.refresh(child1)
    await db_session.refresh(child2)

    # Create another tag to alias to
    alias_target = Tags(title="swimsuit target", desc="", type=TagType.THEME)
    db_session.add(alias_target)
    await db_session.commit()
    await db_session.refresh(alias_target)

    # Login
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin_parent_alias", "password": "AdminPassword123!"},
    )
    access_token = login_response.json()["access_token"]

    # Try to make the parent tag an alias
    response = await client.put(
        f"/api/v1/tags/{parent.tag_id}",
        json={
            "title": "swimwear",
            "type": TagType.THEME,
            "alias_of": alias_target.tag_id,
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "children" in detail.lower()
    assert "bikini child" in detail
    assert "one-piece" in detail
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_tags.py::TestUpdateTag::test_update_tag_rejects_aliasing_parent_with_children -v`
Expected: FAIL

---

### Task 4: Write happy-path tests

**Files:**
- Modify: `tests/api/v1/test_tags.py`

**Step 1: Write the tests**

Add to `TestCreateTag`:

```python
async def test_create_tag_with_canonical_parent_succeeds(
    self, client: AsyncClient, db_session: AsyncSession
):
    """Test that creating a tag with a canonical (non-alias) parent succeeds."""
    # Create TAG_CREATE permission
    perm = Perms(title="tag_create", desc="Create tags")
    db_session.add(perm)
    await db_session.commit()
    await db_session.refresh(perm)

    # Create admin user
    admin = Users(
        username="admin_canon_parent",
        password=get_password_hash("AdminPassword123!"),
        password_type="bcrypt",
        salt="",
        email="admin_canon_parent@example.com",
        active=1,
        admin=1,
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)

    user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
    db_session.add(user_perm)

    # Create canonical parent tag (not an alias)
    parent = Tags(title="swimsuit canon", desc="", type=TagType.THEME)
    db_session.add(parent)
    await db_session.commit()
    await db_session.refresh(parent)

    # Login
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin_canon_parent", "password": "AdminPassword123!"},
    )
    access_token = login_response.json()["access_token"]

    # Create child tag with canonical parent — should succeed
    response = await client.post(
        "/api/v1/tags",
        json={
            "title": "bikini canon",
            "type": TagType.THEME,
            "inheritedfrom_id": parent.tag_id,
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 200
```

Add to `TestUpdateTag`:

```python
async def test_update_tag_alias_without_children_succeeds(
    self, client: AsyncClient, db_session: AsyncSession
):
    """Test that a tag without children can be made into an alias."""
    # Create TAG_UPDATE permission
    perm = Perms(title="tag_update", desc="Update tags")
    db_session.add(perm)
    await db_session.commit()
    await db_session.refresh(perm)

    admin = Users(
        username="admin_alias_ok",
        password=get_password_hash("AdminPassword123!"),
        password_type="bcrypt",
        salt="",
        email="admin_alias_ok@example.com",
        active=1,
        admin=1,
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)

    user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
    db_session.add(user_perm)

    # Create two tags — no parent-child relationship
    canonical = Tags(title="canon no kids", desc="", type=TagType.THEME)
    childless = Tags(title="childless tag", desc="", type=TagType.THEME)
    db_session.add_all([canonical, childless])
    await db_session.commit()
    await db_session.refresh(canonical)
    await db_session.refresh(childless)

    # Login
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin_alias_ok", "password": "AdminPassword123!"},
    )
    access_token = login_response.json()["access_token"]

    # Make childless tag an alias — should succeed
    response = await client.put(
        f"/api/v1/tags/{childless.tag_id}",
        json={
            "title": "childless tag",
            "type": TagType.THEME,
            "alias_of": canonical.tag_id,
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 200
```

**Step 2: Run tests to verify happy paths currently pass**

Run: `uv run pytest tests/api/v1/test_tags.py::TestCreateTag::test_create_tag_with_canonical_parent_succeeds tests/api/v1/test_tags.py::TestUpdateTag::test_update_tag_alias_without_children_succeeds -v`
Expected: PASS (these are happy paths that should already work)

---

### Task 5: Implement `validate_tag_relationships()` and wire into endpoints

**Files:**
- Modify: `app/api/v1/tags.py:1011-1114` (create_tag and update_tag validation sections)

**Step 1: Add the shared validation function**

Add after the existing `get_tag_hierarchy` function (around line 223) in `app/api/v1/tags.py`:

```python
async def validate_tag_relationships(
    db: AsyncSession,
    *,
    tag_id: int | None,
    inheritedfrom_id: int | None,
    alias_of: int | None,
) -> None:
    """Validate parent/alias tag constraints.

    Checks:
    1. Parent tag exists and is not an alias
    2. Alias tag exists
    3. Tag being aliased has no children (update only, when tag_id is set)

    Raises HTTPException(400) on validation failure.
    """
    if inheritedfrom_id:
        parent_result = await db.execute(
            select(Tags).where(Tags.tag_id == inheritedfrom_id)
        )
        parent_tag = parent_result.scalar_one_or_none()
        if not parent_tag:
            raise HTTPException(status_code=400, detail="Parent tag does not exist")
        if parent_tag.alias_of is not None:
            # Look up the canonical tag for a helpful error message
            canonical_result = await db.execute(
                select(Tags).where(Tags.tag_id == parent_tag.alias_of)
            )
            canonical_tag = canonical_result.scalar_one_or_none()
            canonical_name = canonical_tag.title if canonical_tag else "unknown"
            canonical_id = parent_tag.alias_of
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot use alias tag as parent. "
                    f"Tag '{parent_tag.title}' (id: {inheritedfrom_id}) is an alias of "
                    f"'{canonical_name}' (id: {canonical_id}). "
                    f"Use the canonical tag as parent instead."
                ),
            )

    if alias_of:
        alias_result = await db.execute(
            select(Tags).where(Tags.tag_id == alias_of)
        )
        if not alias_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Alias tag does not exist")

    if alias_of and tag_id is not None:
        # Check if this tag has children
        children_result = await db.execute(
            select(Tags.tag_id, Tags.title)
            .where(Tags.inheritedfrom_id == tag_id)
        )
        children = children_result.all()
        if children:
            child_list = ", ".join(
                f"'{title}' (id: {cid})" for cid, title in children
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot make a tag with children into an alias. "
                    f"{len(children)} tag(s) inherit from this tag: {child_list}. "
                    f"Reassign or remove child tags first."
                ),
            )
```

**Step 2: Replace validation in `create_tag`**

Replace lines 1030-1044 (the existing parent/alias validation) with:

```python
    # Validate parent and alias tag relationships
    await validate_tag_relationships(
        db,
        tag_id=None,
        inheritedfrom_id=tag_data.inheritedfrom_id,
        alias_of=tag_data.alias_of,
    )
```

**Step 3: Replace validation in `update_tag`**

Replace lines 1097-1114 (the existing parent/alias validation) with:

```python
    # Validate parent and alias tag relationships
    await validate_tag_relationships(
        db,
        tag_id=tag_id,
        inheritedfrom_id=inheritedfrom_id,
        alias_of=alias_id,
    )
```

Note: keep the `inheritedfrom_id = update_data.get("inheritedfrom_id")` and `alias_id = update_data.get("alias_of")` variable assignments above (lines 1098, 1107) since they're used later.

**Step 4: Run all new tests**

Run: `uv run pytest tests/api/v1/test_tags.py::TestCreateTag::test_create_tag_rejects_alias_as_parent tests/api/v1/test_tags.py::TestCreateTag::test_create_tag_with_canonical_parent_succeeds tests/api/v1/test_tags.py::TestUpdateTag::test_update_tag_rejects_alias_as_parent tests/api/v1/test_tags.py::TestUpdateTag::test_update_tag_rejects_aliasing_parent_with_children tests/api/v1/test_tags.py::TestUpdateTag::test_update_tag_alias_without_children_succeeds -v`
Expected: All PASS

**Step 5: Run full tag test suite to check for regressions**

Run: `uv run pytest tests/api/v1/test_tags.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add app/api/v1/tags.py tests/api/v1/test_tags.py
git commit -m "feat: prevent alias tags from being parents and parent tags from becoming aliases

Add validate_tag_relationships() to enforce:
- Cannot use an alias tag as parent (suggests canonical tag)
- Cannot make a tag with children into an alias (lists children)

Both constraints apply to create and update endpoints."
```

---

### Task 6: Write data audit script

**Files:**
- Create: `scripts/audit_alias_parent_violations.py`

**Step 1: Write the script**

```python
"""
Audit script: find tags where an alias tag is used as a parent.

Reports two types of violations:
1. Tags whose parent (inheritedfrom_id) is an alias tag
2. Tags that are both aliases and parents (have alias_of set AND other tags inherit from them)

Usage: uv run python scripts/audit_alias_parent_violations.py
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_engine, get_db
from app.models.tag import Tags


async def audit():
    async with AsyncSession(async_engine) as db:
        # Violation 1: Tags whose parent is an alias
        # Join: child.inheritedfrom_id -> parent, where parent.alias_of IS NOT NULL
        parent_alias = Tags.__table__.alias("parent")
        result = await db.execute(
            select(
                Tags.tag_id,
                Tags.title,
                Tags.inheritedfrom_id,
                parent_alias.c.title.label("parent_title"),
                parent_alias.c.alias_of.label("parent_alias_of"),
            )
            .join(parent_alias, Tags.inheritedfrom_id == parent_alias.c.tag_id)
            .where(parent_alias.c.alias_of.isnot(None))
        )
        alias_parents = result.all()

        if alias_parents:
            print(f"\n=== Violation 1: {len(alias_parents)} tag(s) with alias parent ===")
            for row in alias_parents:
                print(
                    f"  Tag '{row.title}' (id: {row.tag_id}) "
                    f"has parent '{row.parent_title}' (id: {row.inheritedfrom_id}) "
                    f"which is alias of tag id {row.parent_alias_of}"
                )
        else:
            print("\n=== Violation 1: No tags with alias parents ===")

        # Violation 2: Tags that are aliases AND have children
        # Find tags where alias_of IS NOT NULL and some other tag has inheritedfrom_id = this tag
        child_alias = Tags.__table__.alias("child")
        result = await db.execute(
            select(
                Tags.tag_id,
                Tags.title,
                Tags.alias_of,
            )
            .where(Tags.alias_of.isnot(None))
            .where(
                Tags.tag_id.in_(
                    select(child_alias.c.inheritedfrom_id)
                    .where(child_alias.c.inheritedfrom_id.isnot(None))
                )
            )
        )
        alias_with_children = result.all()

        if alias_with_children:
            print(f"\n=== Violation 2: {len(alias_with_children)} alias tag(s) that are parents ===")
            for row in alias_with_children:
                # Get the children
                children_result = await db.execute(
                    select(Tags.tag_id, Tags.title)
                    .where(Tags.inheritedfrom_id == row.tag_id)
                )
                children = children_result.all()
                child_list = ", ".join(f"'{t}' (id: {i})" for i, t in children)
                print(
                    f"  Tag '{row.title}' (id: {row.tag_id}) "
                    f"is alias of tag id {row.alias_of} "
                    f"but has children: {child_list}"
                )
        else:
            print("\n=== Violation 2: No alias tags with children ===")


if __name__ == "__main__":
    asyncio.run(audit())
```

**Step 2: Run the script against local DB**

Run: `uv run python scripts/audit_alias_parent_violations.py`
Expected: Reports any existing violations (may be zero).

**Step 3: Commit**

```bash
git add scripts/audit_alias_parent_violations.py
git commit -m "feat: add audit script for alias/parent tag violations"
```
