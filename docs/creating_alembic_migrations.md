# Creating Alembic Migrations - Step-by-Step Guide

This guide explains how to create and run Alembic migrations for the shuushuu-api project.

## Overview

Alembic is a database migration tool for SQLAlchemy. It allows you to:
- Version control your database schema
- Apply incremental schema changes
- Roll back changes if needed
- Keep development, staging, and production databases in sync

## Configuration

Your Alembic setup:
- **Config location:** `pyproject.toml` ([tool.alembic] section)
- **Script location:** `alembic/` directory
- **Versions directory:** `alembic/versions/`
- **Database URL:** Set dynamically in `alembic/env.py` from `settings.DATABASE_URL_SYNC`

## Step-by-Step: Creating a Migration

### Step 1: Generate a Migration File

Use the `alembic revision` command to create a new migration:

```bash
# Basic syntax
alembic revision -m "description of change"

# Example: Creating the FULLTEXT index
alembic revision -m "add fulltext index to posts.post_text"
```

This creates a new file in `alembic/versions/` with a name like:
```
abc123def456_add_fulltext_index_to_posts_post_text.py
```

The filename format is: `{revision_id}_{description}.py`

### Step 2: Edit the Migration File

Open the generated file and add your schema changes in the `upgrade()` and `downgrade()` functions.

**Example for FULLTEXT index:**

```python
"""add fulltext index to posts.post_text

Revision ID: abc123def456
Revises: 8d66158eb568
Create Date: 2025-11-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'abc123def456'
down_revision: Union[str, Sequence[str], None] = '8d66158eb568'  # Previous migration
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add FULLTEXT index to posts.post_text for faster comment searching."""
    # Use raw SQL for MySQL-specific FULLTEXT index
    op.execute(
        "CREATE FULLTEXT INDEX idx_post_text_fulltext ON posts(post_text)"
    )


def downgrade() -> None:
    """Remove FULLTEXT index from posts.post_text."""
    op.execute(
        "DROP INDEX idx_post_text_fulltext ON posts"
    )
```

**Key points:**
- `revision`: Auto-generated unique ID for this migration
- `down_revision`: Points to the previous migration (creates a chain)
- `upgrade()`: Apply the changes (moving forward)
- `downgrade()`: Revert the changes (rolling back)

### Step 3: Review the Migration

Before running, verify:
1. The `down_revision` points to the correct previous migration
2. The `upgrade()` function has the correct SQL
3. The `downgrade()` function properly reverses the changes
4. Test the SQL syntax separately if needed

### Step 4: Run the Migration

```bash
# Apply all pending migrations
alembic upgrade head

# Or apply just one migration at a time
alembic upgrade +1
```

Expected output:
```
INFO  [alembic.runtime.migration] Running upgrade 8d66158eb568 -> abc123def456, add fulltext index to posts.post_text
```

### Step 5: Verify the Change

Check that the index was created:

```bash
# Connect to MySQL
mysql -u your_user -p your_database

# Verify the index
SHOW INDEX FROM posts WHERE Key_name = 'idx_post_text_fulltext';
```

## Common Migration Tasks

### Creating a FULLTEXT Index

```python
def upgrade() -> None:
    op.execute(
        "CREATE FULLTEXT INDEX idx_column_fulltext ON table_name(column_name)"
    )

def downgrade() -> None:
    op.execute("DROP INDEX idx_column_fulltext ON table_name")
```

### Adding a Column

```python
def upgrade() -> None:
    op.add_column('table_name',
        sa.Column('new_column', sa.String(255), nullable=True)
    )

def downgrade() -> None:
    op.drop_column('table_name', 'new_column')
```

### Creating a Regular Index

```python
def upgrade() -> None:
    op.create_index(
        'idx_column_name',  # Index name
        'table_name',       # Table name
        ['column_name'],    # Column(s) to index
        unique=False        # Whether unique
    )

def downgrade() -> None:
    op.drop_index('idx_column_name', table_name='table_name')
```

### Creating a Foreign Key

```python
def upgrade() -> None:
    op.create_foreign_key(
        'fk_table_ref',       # Constraint name
        'table_name',         # Source table
        'referenced_table',   # Target table
        ['column_id'],        # Source column(s)
        ['id'],              # Target column(s)
        ondelete='CASCADE'   # Optional: ON DELETE behavior
    )

def downgrade() -> None:
    op.drop_constraint('fk_table_ref', 'table_name', type_='foreignkey')
```

### Modifying a Column

```python
def upgrade() -> None:
    op.alter_column(
        'table_name',
        'column_name',
        type_=sa.String(500),  # New type
        nullable=False         # New nullable setting
    )

def downgrade() -> None:
    op.alter_column(
        'table_name',
        'column_name',
        type_=sa.String(255),  # Original type
        nullable=True          # Original nullable setting
    )
```

## Useful Alembic Commands

```bash
# Show current database version
alembic current

# Show migration history
alembic history --verbose

# Show pending migrations
alembic history --indicate-current

# Upgrade to specific revision
alembic upgrade abc123def456

# Downgrade one step
alembic downgrade -1

# Downgrade to specific revision
alembic downgrade 8d66158eb568

# Downgrade everything (caution!)
alembic downgrade base

# Show SQL without running it
alembic upgrade head --sql

# Stamp database as being at a specific version (without running migrations)
alembic stamp head
```

## Best Practices

### 1. Always Test Migrations

Test migrations in a development environment before production:

```bash
# Test upgrade
alembic upgrade head

# Test downgrade
alembic downgrade -1

# Test upgrade again
alembic upgrade head
```

### 2. Use Descriptive Names

Good: `add_fulltext_index_to_posts_post_text`
Bad: `update_posts`, `migration_001`

### 3. Keep Migrations Small

One logical change per migration makes it easier to:
- Review changes
- Revert if needed
- Debug issues

### 4. Test Downgrade Functions

Always test that your downgrade() actually works:
```bash
alembic upgrade head
alembic downgrade -1  # Should cleanly reverse the change
alembic upgrade head  # Should re-apply successfully
```

### 5. Never Edit Deployed Migrations

Once a migration is deployed to production, never edit it. Create a new migration instead.

### 6. Handle Data Migrations Carefully

For migrations that modify data (not just schema), consider:
- Adding `batch_size` for large tables
- Using transactions
- Adding progress logging

Example:
```python
def upgrade() -> None:
    connection = op.get_bind()

    # Batch update to avoid locking entire table
    connection.execute(sa.text("""
        UPDATE posts
        SET normalized_text = LOWER(post_text)
        LIMIT 1000
    """))
```

## Troubleshooting

### "No such revision" Error

**Cause:** Alembic can't find the migration file or the revision ID is wrong.

**Solution:**
```bash
# Check current state
alembic current

# Check history
alembic history

# If out of sync, stamp to correct version
alembic stamp head
```

### "Can't locate revision identified by" Error

**Cause:** The `down_revision` in your migration doesn't match any existing revision.

**Solution:**
1. Check `alembic history` to find the latest revision ID
2. Update your migration's `down_revision` to match

### Migration Fails Halfway

**Cause:** SQL error or constraint violation.

**Solution:**
1. Check the error message
2. Fix the issue manually in the database if needed
3. Mark the migration as complete: `alembic stamp head`
4. Or rollback and fix the migration: `alembic downgrade -1`

### FULLTEXT Index Already Exists

**Cause:** Index was created manually or migration ran twice.

**Solution:**
```sql
-- Check if index exists
SHOW INDEX FROM posts WHERE Key_name = 'idx_post_text_fulltext';

-- Drop it if it exists
DROP INDEX idx_post_text_fulltext ON posts;

-- Then re-run migration
```

## Migration Workflow Example

Complete workflow for adding the FULLTEXT index:

```bash
# 1. Create migration
alembic revision -m "add fulltext index to posts.post_text"

# 2. Edit the generated file (add upgrade/downgrade code)
# See example above

# 3. Review the migration
cat alembic/versions/abc123def456_add_fulltext_index_to_posts_post_text.py

# 4. Test in development database
alembic upgrade head

# 5. Verify the index was created
mysql -u user -p database -e "SHOW INDEX FROM posts WHERE Key_name = 'idx_post_text_fulltext'"

# 6. Test rollback
alembic downgrade -1

# 7. Verify index was removed
mysql -u user -p database -e "SHOW INDEX FROM posts WHERE Key_name = 'idx_post_text_fulltext'"

# 8. Re-apply for final verification
alembic upgrade head

# 9. Commit the migration file to version control
git add alembic/versions/abc123def456_add_fulltext_index_to_posts_post_text.py
git commit -m "Add FULLTEXT index migration for comment search"

# 10. Deploy to production
# On production server:
alembic upgrade head
```

## Auto-generating Migrations (Advanced)

Alembic can auto-detect changes between your models and database:

```bash
# Auto-generate migration based on model changes
alembic revision --autogenerate -m "description"
```

**Important:** Always review auto-generated migrations! They may:
- Miss some changes (like custom indexes)
- Include unwanted changes
- Need manual adjustments for complex scenarios

For this project, manual migrations are recommended for full control.
