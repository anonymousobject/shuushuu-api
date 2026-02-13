"""
SQLModel-based Permission models with inheritance for security

This module defines the permissions system database models using SQLModel:
- Groups: User groups with specific permissions
- Perms: Individual permissions
- GroupPerms: Junction table linking groups to permissions
- UserGroups: Junction table linking users to groups
- UserPerms: Junction table linking users to individual permissions

This approach eliminates field duplication while maintaining security boundaries.
"""

from sqlalchemy import ForeignKeyConstraint, Index
from sqlmodel import Field, Relationship, SQLModel

# ===== Groups =====


class GroupBase(SQLModel):
    """
    Base model with shared public fields for Groups.

    These fields are safe to expose via the API.
    """

    title: str | None = Field(default=None, max_length=50)
    desc: str | None = Field(default=None, max_length=75)


class Groups(GroupBase, table=True):
    """
    Database table for user groups.

    Groups are collections of permissions that can be assigned to users.
    """

    __tablename__ = "groups"

    # Primary key
    group_id: int | None = Field(default=None, primary_key=True)

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.


# ===== Perms =====


class PermBase(SQLModel):
    """
    Base model with shared public fields for Perms.

    These fields are safe to expose via the API.
    """

    title: str | None = Field(default=None, max_length=50)
    desc: str | None = Field(default=None, max_length=75)


class Perms(PermBase, table=True):
    """
    Database table for individual permissions.

    Permissions define specific actions users can perform.
    """

    __tablename__ = "perms"

    # Primary key
    perm_id: int | None = Field(default=None, primary_key=True)

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.


# ===== GroupPerms (Junction Table) =====


class GroupPermBase(SQLModel):
    """
    Base model with shared public fields for GroupPerms.

    Junction table linking groups to permissions with a permission value.
    """

    group_id: int = Field(foreign_key="groups.group_id", primary_key=True)
    perm_id: int = Field(foreign_key="perms.perm_id", primary_key=True)
    permvalue: int | None = Field(default=None)


class GroupPerms(GroupPermBase, table=True):
    """
    Database table linking groups to permissions.

    This is a junction table with a composite primary key and a permission value.
    """

    __tablename__ = "group_perms"

    __table_args__ = (
        ForeignKeyConstraint(
            ["group_id"],
            ["groups.group_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_group_perms_group_id",
        ),
        ForeignKeyConstraint(
            ["perm_id"],
            ["perms.perm_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_group_perms_perm_id",
        ),
        Index("fk_group_perms_perm_id", "perm_id"),
    )

    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.


# ===== UserGroups (Junction Table) =====


class UserGroupBase(SQLModel):
    """
    Base model with shared public fields for UserGroups.

    Junction table linking users to groups.
    """

    user_id: int = Field(primary_key=True)
    group_id: int = Field(primary_key=True, foreign_key="groups.group_id")


class UserGroups(UserGroupBase, table=True):
    """
    Database table linking users to groups.

    This is a simple junction table with a composite primary key.
    """

    __tablename__ = "user_groups"

    # Relationship to Groups (requires explicit eager loading via selectinload/joinedload)
    group: Groups = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[UserGroups.group_id]",
            "lazy": "raise",
        }
    )


# ===== UserPerms (Junction Table) =====


class UserPermBase(SQLModel):
    """
    Base model with shared public fields for UserPerms.

    Junction table linking users to individual permissions.
    """

    user_id: int = Field(primary_key=True)
    perm_id: int = Field(primary_key=True)
    permvalue: int = Field(default=1)


class UserPerms(UserPermBase, table=True):
    """
    Database table linking users to individual permissions.

    Allows for user-specific permission overrides beyond group permissions.
    """

    __tablename__ = "user_perms"

    # Note: No foreign key constraints in schema, but logically references users and perms
    # Note: Relationships are intentionally omitted.
    # Foreign keys are sufficient for queries, and omitting relationships avoids:
    # - Circular import issues
    # - Accidental eager loading
    # - Unwanted auto-serialization in API responses
    # If needed, relationships can be added selectively with proper lazy loading.
