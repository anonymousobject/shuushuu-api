"""Drop the redundant unnamed-FK twins the baseline inherited.

The models declared 34 foreign keys twice — ``Field(foreign_key=...)`` plus a
named ``ForeignKeyConstraint`` carrying the intended ON DELETE — and the
baseline's create_all capture faithfully rendered both. Postgres enforces
both, and the auto-named twin defaults to NO ACTION, which vetoes the named
constraint's CASCADE/SET NULL on every delete. The models now declare each FK
once (the named constraint); this drops the twins so the chain matches.

Guarded by tests/integration/test_fk_constraint_names.py
(test_one_fk_constraint_per_column_set).

Revision ID: 0002_drop_redundant_fk_twins
Revises: 0001_pg_baseline
Create Date: 2026-08-29
"""

from alembic import op

revision = "0002_drop_redundant_fk_twins"
down_revision = "0001_pg_baseline"
branch_labels = None
depends_on = None

# (constraint, table, column, referent table, referent column) — the unnamed
# FOREIGN KEY clauses in 0001_pg_baseline.sql, under the names Postgres
# auto-assigned them. Each has a named fk_* twin that stays.
_REDUNDANT_TWINS = [
    ("bans_banned_by_fkey", "bans", "banned_by", "users", "user_id"),
    ("bans_user_id_fkey", "bans", "user_id", "users", "user_id"),
    ("favorites_image_id_fkey", "favorites", "image_id", "images", "image_id"),
    ("favorites_user_id_fkey", "favorites", "user_id", "users", "user_id"),
    ("group_perms_group_id_fkey", "group_perms", "group_id", "groups", "group_id"),
    ("group_perms_perm_id_fkey", "group_perms", "perm_id", "perms", "perm_id"),
    ("image_ratings_image_id_fkey", "image_ratings", "image_id", "images", "image_id"),
    ("image_ratings_user_id_fkey", "image_ratings", "user_id", "users", "user_id"),
    ("images_replacement_id_fkey", "images", "replacement_id", "images", "image_id"),
    ("images_status_user_id_fkey", "images", "status_user_id", "users", "user_id"),
    ("images_user_id_fkey", "images", "user_id", "users", "user_id"),
    ("news_user_id_fkey", "news", "user_id", "users", "user_id"),
    ("posts_last_updated_user_id_fkey", "posts", "last_updated_user_id", "users", "user_id"),
    ("posts_user_id_fkey", "posts", "user_id", "users", "user_id"),
    ("privmsgs_from_user_id_fkey", "privmsgs", "from_user_id", "users", "user_id"),
    ("privmsgs_to_user_id_fkey", "privmsgs", "to_user_id", "users", "user_id"),
    ("quicklinks_user_id_fkey", "quicklinks", "user_id", "users", "user_id"),
    ("refresh_tokens_user_id_fkey", "refresh_tokens", "user_id", "users", "user_id"),
    ("tag_external_links_tag_id_fkey", "tag_external_links", "tag_id", "tags", "tag_id"),
    ("tag_history_image_id_fkey", "tag_history", "image_id", "images", "image_id"),
    ("tag_history_tag_id_fkey", "tag_history", "tag_id", "tags", "tag_id"),
    ("tag_history_user_id_fkey", "tag_history", "user_id", "users", "user_id"),
    ("tag_links_image_id_fkey", "tag_links", "image_id", "images", "image_id"),
    ("tag_links_tag_id_fkey", "tag_links", "tag_id", "tags", "tag_id"),
    ("tag_links_user_id_fkey", "tag_links", "user_id", "users", "user_id"),
    ("tags_alias_of_fkey", "tags", "alias_of", "tags", "tag_id"),
    ("tags_inheritedfrom_id_fkey", "tags", "inheritedfrom_id", "tags", "tag_id"),
    ("tags_user_id_fkey", "tags", "user_id", "users", "user_id"),
    ("user_banner_pins_banner_id_fkey", "user_banner_pins", "banner_id", "banners", "banner_id"),
    ("user_banner_pins_user_id_fkey", "user_banner_pins", "user_id", "users", "user_id"),
    (
        "user_banner_preferences_user_id_fkey",
        "user_banner_preferences",
        "user_id",
        "users",
        "user_id",
    ),
    ("user_suspensions_actioned_by_fkey", "user_suspensions", "actioned_by", "users", "user_id"),
    ("user_suspensions_user_id_fkey", "user_suspensions", "user_id", "users", "user_id"),
    ("users_bookmark_fkey", "users", "bookmark", "images", "image_id"),
]


def upgrade() -> None:
    for constraint, table, _column, _ref_table, _ref_column in _REDUNDANT_TWINS:
        op.drop_constraint(constraint, table, type_="foreignkey")


def downgrade() -> None:
    # Restores the exact baseline state: unnamed clauses default to NO ACTION.
    for constraint, table, column, ref_table, ref_column in _REDUNDANT_TWINS:
        op.create_foreign_key(constraint, table, ref_table, [column], [ref_column])
