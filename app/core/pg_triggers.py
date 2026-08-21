"""Counter-maintenance triggers for Postgres.

Ports the MariaDB trigger set (migrations 2cd4e874e956, 5721ccce6a85,
ec5c5fa4e3e5) that maintains the denormalized counters:

- ``tags.usage_count``            <- tag_links INSERT/DELETE
- ``images.favorites``            <- favorites INSERT/DELETE/UPDATE (re-point)
- ``users.favorites``             <- favorites INSERT/DELETE/UPDATE (re-point)
- ``users.image_posts``           <- images INSERT/DELETE/UPDATE (re-point)
- ``images.posts``/``last_post``  <- posts INSERT/UPDATE/DELETE, soft-delete aware
- ``users.posts``                 <- posts INSERT/UPDATE/DELETE, soft-delete aware

Layout differs from MariaDB deliberately: one function per (source table,
event) covering every counter that event touches, instead of one trigger per
target table — same semantics, fewer objects, and each event's full effect
reads in one place.

Semantics note: Postgres fires these on FK-cascaded deletes, which InnoDB
does not. The counter drift MariaDB accumulates when an image is deleted
(cascaded tag_links/favorites/posts rows never fire its triggers) does not
occur here; the UPDATE a cascade-fired trigger aims at the row being deleted
is a harmless no-op.

The DDL is idempotent (CREATE OR REPLACE FUNCTION + DROP TRIGGER IF EXISTS)
so it can be applied to a live database as well as a fresh bootstrap. Until a
Postgres Alembic baseline exists, installation happens in build_pg_schema;
the baseline migration inherits this file's SQL when it lands.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


def _trigger(name: str, event: str, table: str, body: str) -> tuple[str, str, str]:
    # Three separate statements: asyncpg refuses multiple commands in one
    # prepared statement (the semicolons inside $$..$$ are fine).
    return (
        f"""
        CREATE OR REPLACE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        {body}
        RETURN NULL;
        END $$
        """,
        f"DROP TRIGGER IF EXISTS {name} ON {table}",
        f"""
        CREATE TRIGGER {name} AFTER {event} ON {table}
            FOR EACH ROW EXECUTE FUNCTION {name}()
        """,
    )


_TRIGGER_TRIOS = (
    _trigger(
        "tag_links_counters_insert",
        "INSERT",
        "tag_links",
        "UPDATE tags SET usage_count = usage_count + 1 WHERE tag_id = NEW.tag_id;",
    ),
    _trigger(
        "tag_links_counters_delete",
        "DELETE",
        "tag_links",
        "UPDATE tags SET usage_count = GREATEST(0, usage_count - 1) WHERE tag_id = OLD.tag_id;",
    ),
    _trigger(
        "favorites_counters_insert",
        "INSERT",
        "favorites",
        """
        UPDATE images SET favorites = favorites + 1 WHERE image_id = NEW.image_id;
        UPDATE users SET favorites = favorites + 1 WHERE user_id = NEW.user_id;
        """,
    ),
    _trigger(
        "favorites_counters_delete",
        "DELETE",
        "favorites",
        """
        UPDATE images SET favorites = favorites - 1 WHERE image_id = OLD.image_id;
        UPDATE users SET favorites = favorites - 1 WHERE user_id = OLD.user_id;
        """,
    ),
    _trigger(
        "favorites_counters_update",
        "UPDATE",
        "favorites",
        """
        IF OLD.image_id IS DISTINCT FROM NEW.image_id THEN
            UPDATE images SET favorites = favorites - 1 WHERE image_id = OLD.image_id;
            UPDATE images SET favorites = favorites + 1 WHERE image_id = NEW.image_id;
        END IF;
        IF OLD.user_id IS DISTINCT FROM NEW.user_id THEN
            UPDATE users SET favorites = favorites - 1 WHERE user_id = OLD.user_id;
            UPDATE users SET favorites = favorites + 1 WHERE user_id = NEW.user_id;
        END IF;
        """,
    ),
    _trigger(
        "images_counters_insert",
        "INSERT",
        "images",
        "UPDATE users SET image_posts = image_posts + 1 WHERE user_id = NEW.user_id;",
    ),
    _trigger(
        "images_counters_delete",
        "DELETE",
        "images",
        "UPDATE users SET image_posts = image_posts - 1 WHERE user_id = OLD.user_id;",
    ),
    _trigger(
        "images_counters_update",
        "UPDATE",
        "images",
        """
        IF OLD.user_id IS DISTINCT FROM NEW.user_id THEN
            UPDATE users SET image_posts = image_posts - 1 WHERE user_id = OLD.user_id;
            UPDATE users SET image_posts = image_posts + 1 WHERE user_id = NEW.user_id;
        END IF;
        """,
    ),
    # posts triggers are soft-delete aware: only rows with deleted = false
    # count, and images.last_post tracks MAX(date) of the visible rows.
    _trigger(
        "posts_counters_insert",
        "INSERT",
        "posts",
        """
        IF NOT NEW.deleted THEN
            UPDATE images SET posts = posts + 1, last_post = NEW.date
                WHERE image_id = NEW.image_id;
            UPDATE users SET posts = posts + 1 WHERE user_id = NEW.user_id;
        END IF;
        """,
    ),
    _trigger(
        "posts_counters_update",
        "UPDATE",
        "posts",
        """
        IF OLD.deleted = false AND NEW.deleted = true THEN
            UPDATE images
            SET posts = posts - 1,
                last_post = (SELECT MAX(date) FROM posts
                             WHERE image_id = NEW.image_id AND NOT deleted)
            WHERE image_id = NEW.image_id;
            UPDATE users SET posts = posts - 1 WHERE user_id = NEW.user_id;
        ELSIF OLD.deleted = true AND NEW.deleted = false THEN
            UPDATE images
            SET posts = posts + 1,
                last_post = (SELECT MAX(date) FROM posts
                             WHERE image_id = NEW.image_id AND NOT deleted)
            WHERE image_id = NEW.image_id;
            UPDATE users SET posts = posts + 1 WHERE user_id = NEW.user_id;
        ELSE
            IF OLD.image_id IS DISTINCT FROM NEW.image_id THEN
                UPDATE images
                SET posts = posts - 1,
                    last_post = (SELECT MAX(date) FROM posts
                                 WHERE image_id = OLD.image_id AND NOT deleted)
                WHERE image_id = OLD.image_id;
                UPDATE images
                SET posts = posts + 1,
                    last_post = (SELECT MAX(date) FROM posts
                                 WHERE image_id = NEW.image_id AND NOT deleted)
                WHERE image_id = NEW.image_id;
            END IF;
            IF OLD.user_id IS DISTINCT FROM NEW.user_id THEN
                UPDATE users SET posts = posts - 1 WHERE user_id = OLD.user_id;
                UPDATE users SET posts = posts + 1 WHERE user_id = NEW.user_id;
            END IF;
        END IF;
        """,
    ),
    _trigger(
        "posts_counters_delete",
        "DELETE",
        "posts",
        """
        IF NOT OLD.deleted THEN
            UPDATE images
            SET posts = posts - 1,
                last_post = (SELECT MAX(date) FROM posts
                             WHERE image_id = OLD.image_id AND NOT deleted)
            WHERE image_id = OLD.image_id;
            UPDATE users SET posts = posts - 1 WHERE user_id = OLD.user_id;
        END IF;
        """,
    ),
)


_TRIGGER_DDL: tuple[str, ...] = tuple(stmt for trio in _TRIGGER_TRIOS for stmt in trio)


async def install_counter_triggers(conn: AsyncConnection) -> None:
    """Install (or refresh) the counter triggers. Idempotent."""
    for ddl in _TRIGGER_DDL:
        await conn.execute(text(ddl))
