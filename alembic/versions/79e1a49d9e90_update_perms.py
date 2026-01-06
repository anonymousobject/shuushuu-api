"""Update permissions to follow {OBJECT}_{ACTION} naming convention
Also removes deprecated permissions and adds new image tag management perms.

Revision ID: 79e1a49d9e90
Revises: b8df3d41cab8
Create Date: 2025-11-20 22:46:09.980720

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '79e1a49d9e90'
down_revision: Union[str, Sequence[str], None] = 'b8df3d41cab8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

deprecated_perms = [
    'image_check_dupes',
    'image_edit_filename',
    'taggerlevel',
    'modlevel',
    'adminlevel'
]

def upgrade() -> None:
    """Rename permissions to follow {OBJECT}_{ACTION} naming convention."""
    # Tag permissions
    op.execute("UPDATE perms SET title = 'tag_create' WHERE title = 'createtag'")
    op.execute("UPDATE perms SET title = 'tag_edit' WHERE title = 'edittag'")
    op.execute("UPDATE perms SET title = 'tag_update' WHERE title = 'renametag'")
    op.execute("UPDATE perms SET title = 'tag_delete' WHERE title = 'deletetag'")

    # Image permissions
    op.execute("UPDATE perms SET title = 'image_edit_meta' WHERE title = 'editimgmeta'")
    op.execute("UPDATE perms SET title = 'image_edit' WHERE title = 'editimg'")
    op.execute("UPDATE perms SET title = 'image_mark_repost' WHERE title = 'repost'")

    # User/Group permissions
    op.execute("UPDATE perms SET title = 'group_manage' WHERE title = 'allgroup'")
    op.execute("UPDATE perms SET title = 'group_perm_manage' WHERE title = 'allgroupperm'")
    op.execute("UPDATE perms SET title = 'user_edit_profile' WHERE title = 'editprofile'")
    op.execute("UPDATE perms SET title = 'user_ban' WHERE title = 'ban'")

    # Content moderation
    op.execute("UPDATE perms SET title = 'post_edit' WHERE title = 'editpost'")

    # Special permissions
    op.execute("UPDATE perms SET title = 'theme_edit' WHERE title = 'themeeditor'")
    op.execute("UPDATE perms SET title = 'rating_revoke' WHERE title = 'revokerating'")
    op.execute("UPDATE perms SET title = 'report_revoke' WHERE title = 'revokereports'")

    # Remove deprecated permissions if they exist (must delete FK references first)
    for perm in deprecated_perms:
        op.execute(f"""
            DELETE gp FROM group_perms gp
            JOIN perms p ON gp.perm_id = p.perm_id
            WHERE p.title = '{perm}'
        """)
        op.execute(f"""
            DELETE up FROM user_perms up
            JOIN perms p ON up.perm_id = p.perm_id
            WHERE p.title = '{perm}'
        """)
        op.execute(f"DELETE FROM perms WHERE title = '{perm}'")

    # Add new perms
    op.execute("INSERT INTO perms (title, `desc`) VALUES ('image_tag_add', 'Add tags to images')")
    op.execute("INSERT INTO perms (title, `desc`) VALUES ('image_tag_remove', 'Remove tags from images')")
    op.execute("INSERT INTO perms (title, `desc`) VALUES ('privmsg_view', 'View private messages')")
    op.execute("INSERT INTO perms (title, `desc`) VALUES ('image_delete', 'Delete images')")

    # Grant admins group the IMAGE_DELETE permission
    op.execute("""
        INSERT INTO group_perms (group_id, perm_id, permvalue)
        SELECT g.group_id, p.perm_id, 1
        FROM groups g, perms p
        WHERE g.title = 'admins' AND p.title = 'image_delete'
    """)


def downgrade() -> None:
    """Revert permissions to original naming convention."""
    # Tag permissions
    op.execute("UPDATE perms SET title = 'createtag' WHERE title = 'tag_create'")
    op.execute("UPDATE perms SET title = 'edittag' WHERE title = 'tag_edit'")
    op.execute("UPDATE perms SET title = 'renametag' WHERE title = 'tag_update'")
    op.execute("UPDATE perms SET title = 'deletetag' WHERE title = 'tag_delete'")

    # Image permissions
    op.execute("UPDATE perms SET title = 'editimgmeta' WHERE title = 'image_edit_meta'")
    op.execute("UPDATE perms SET title = 'editimg' WHERE title = 'image_edit'")
    op.execute("UPDATE perms SET title = 'repost' WHERE title = 'image_mark_repost'")

    # User/Group permissions
    op.execute("UPDATE perms SET title = 'allgroup' WHERE title = 'group_manage'")
    op.execute("UPDATE perms SET title = 'allgroupperm' WHERE title = 'group_perm_manage'")
    op.execute("UPDATE perms SET title = 'editprofile' WHERE title = 'user_edit_profile'")
    op.execute("UPDATE perms SET title = 'ban' WHERE title = 'user_ban'")

    # Content moderation
    op.execute("UPDATE perms SET title = 'editpost' WHERE title = 'post_edit'")

    # Special permissions
    op.execute("UPDATE perms SET title = 'themeeditor' WHERE title = 'theme_edit'")
    op.execute("UPDATE perms SET title = 'revokerating' WHERE title = 'rating_revoke'")
    op.execute("UPDATE perms SET title = 'revokereports' WHERE title = 'report_revoke'")

    for perm in deprecated_perms:
        # Re-add deprecated permissions with generic descriptions
        op.execute(
            f"INSERT INTO perms (title, `desc`) VALUES ('{perm}', 'Deprecated permission {perm}')"
        )

    # Remove newly added perms (must delete FK references first)
    new_perms = ['image_tag_add', 'image_tag_remove', 'privmsg_view', 'image_delete']
    for perm in new_perms:
        op.execute(f"""
            DELETE gp FROM group_perms gp
            JOIN perms p ON gp.perm_id = p.perm_id
            WHERE p.title = '{perm}'
        """)
        op.execute(f"""
            DELETE up FROM user_perms up
            JOIN perms p ON up.perm_id = p.perm_id
            WHERE p.title = '{perm}'
        """)
        op.execute(f"DELETE FROM perms WHERE title = '{perm}'")
