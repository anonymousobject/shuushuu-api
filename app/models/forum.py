"""
SQLModel-based Forum models.

Four tables: forum_categories, forum_threads, forum_posts, forum_thread_reads.
ForumPosts mirrors the Comments model shape (soft-delete, edit tracking, raw
markdown in post_text) so rendering and moderation work identically.

forum_threads carries denormalized post_count/last_post_at/last_post_user_id
maintained by app.services.forum.recompute_thread_stats — always recomputed
from live posts inside the mutating transaction, never incremented.
"""

from datetime import datetime

from sqlalchemy import Column, ForeignKeyConstraint, Index, Text, text
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime


class ForumCategories(SQLModel, table=True):
    """Forum category. The *_perm columns hold a permission title required for
    that action (values restricted to FORUM_ACCESS_PERMISSIONS at the API layer);
    NULL means view=public (incl. logged-out) / create+reply=any logged-in user."""

    __tablename__ = "forum_categories"

    category_id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=100, unique=True)
    description: str | None = Field(default=None, max_length=500)
    sort_order: int = Field(default=0)
    view_perm: str | None = Field(default=None, max_length=64)
    thread_create_perm: str | None = Field(default=None, max_length=64)
    reply_perm: str | None = Field(default=None, max_length=64)


class ForumThreads(SQLModel, table=True):
    """Forum thread. The opening post is the thread's first forum_posts row."""

    __tablename__ = "forum_threads"

    __table_args__ = (
        ForeignKeyConstraint(
            ["category_id"],
            ["forum_categories.category_id"],
            ondelete="RESTRICT",
            onupdate="CASCADE",
            name="fk_forum_threads_category_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_threads_user_id",
        ),
        ForeignKeyConstraint(
            ["last_post_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_forum_threads_last_post_user_id",
        ),
        Index("idx_forum_threads_list", "category_id", "pinned", "last_post_at"),
        Index("fk_forum_threads_user_id", "user_id"),
        Index("fk_forum_threads_last_post_user_id", "last_post_user_id"),
    )

    thread_id: int | None = Field(default=None, primary_key=True)
    category_id: int
    title: str = Field(max_length=255)
    user_id: int
    date: datetime = Field(
        sa_column=Column(UtcDateTime, nullable=False, server_default=text("current_timestamp()"))
    )
    pinned: bool = Field(default=False)
    locked: bool = Field(default=False)
    deleted: bool = Field(default=False, index=True)

    # Denormalized from forum_posts; see recompute_thread_stats
    post_count: int = Field(default=0)
    last_post_at: datetime | None = Field(
        default=None, sa_column=Column(UtcDateTime, nullable=True)
    )
    last_post_user_id: int | None = Field(default=None)


class ForumPosts(SQLModel, table=True):
    """Forum post; field shape mirrors Comments (table 'posts')."""

    __tablename__ = "forum_posts"

    __table_args__ = (
        ForeignKeyConstraint(
            ["thread_id"],
            ["forum_threads.thread_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_posts_thread_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_posts_user_id",
        ),
        ForeignKeyConstraint(
            ["last_updated_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_forum_posts_last_updated_user_id",
        ),
        Index("idx_forum_posts_thread_date", "thread_id", "date"),
        Index("fk_forum_posts_user_id", "user_id"),
        Index("fk_forum_posts_last_updated_user_id", "last_updated_user_id"),
    )

    post_id: int | None = Field(default=None, primary_key=True)
    thread_id: int
    user_id: int
    post_text: str = Field(default="", sa_column=Column(Text, nullable=False))
    date: datetime = Field(
        sa_column=Column(UtcDateTime, nullable=False, server_default=text("current_timestamp()"))
    )

    # Soft-delete flag
    deleted: bool = Field(default=False, index=True)

    # Public update tracking
    update_count: int = Field(default=0)

    # Internal tracking fields (privacy-sensitive)
    ip: str = Field(default="", max_length=45)

    # Internal moderation fields
    last_updated: datetime | None = Field(
        default=None, sa_column=Column(UtcDateTime, nullable=True)
    )
    last_updated_user_id: int | None = Field(default=None)


class ForumThreadReads(SQLModel, table=True):
    """Per-user read position. A thread is unread when the user has no row or
    last_read_at < thread.last_post_at."""

    __tablename__ = "forum_thread_reads"

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_thread_reads_user_id",
        ),
        ForeignKeyConstraint(
            ["thread_id"],
            ["forum_threads.thread_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_forum_thread_reads_thread_id",
        ),
        Index("fk_forum_thread_reads_thread_id", "thread_id"),
    )

    user_id: int | None = Field(default=None, primary_key=True)
    thread_id: int | None = Field(default=None, primary_key=True)
    last_read_at: datetime = Field(sa_column=Column(UtcDateTime, nullable=False))
