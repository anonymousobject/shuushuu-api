import datetime
import decimal
from typing import Optional

from sqlalchemy import (
    CHAR,
    DECIMAL,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKeyConstraint,
    Index,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.mysql import (
    # CHAR,
    # DECIMAL,
    INTEGER,
    LONGTEXT,
    MEDIUMINT,
    SMALLINT,
    TINYINT,
    TINYTEXT,
    VARCHAR,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Banners(Base):
    __tablename__ = "banners"

    banner_id: Mapped[int] = mapped_column(SMALLINT(4), primary_key=True)
    path: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    author: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    leftext: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default=text("'png'"))
    midext: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default=text("'png'"))
    rightext: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default=text("'png'"))
    full: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    event_id: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("0"))
    active: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("1"))
    date: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, server_default=text("current_timestamp()")
    )


t_donations = Table(
    "donations",
    Base.metadata,
    Column("date", DateTime, nullable=False, server_default=text("current_timestamp()")),
    Column("user_id", INTEGER(10)),
    Column("nick", String(30)),
    Column("amount", INTEGER(3)),
    Index("idx_date", "date"),
)


class EvaTheme(Base):
    __tablename__ = "eva_theme"

    theme_id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    active_month_from: Mapped[int] = mapped_column(
        TINYINT(2), nullable=False, server_default=text("0")
    )
    active_month_to: Mapped[int] = mapped_column(
        TINYINT(2), nullable=False, server_default=text("0")
    )
    active_day_from: Mapped[int] = mapped_column(
        TINYINT(2), nullable=False, server_default=text("0")
    )
    active_day_to: Mapped[int] = mapped_column(TINYINT(2), nullable=False, server_default=text("0"))
    active: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    theme_name: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    banner: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    theme_content: Mapped[str | None] = mapped_column(LONGTEXT)


class Groups(Base):
    __tablename__ = "groups"

    group_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(50))
    desc: Mapped[str | None] = mapped_column(String(75))

    group_perms: Mapped[list["GroupPerms"]] = relationship("GroupPerms", back_populates="group")


t_image_ratings_avg = Table(
    "image_ratings_avg", Base.metadata, Column("type", CHAR(3)), Column("avg", Float)
)


class Images(Base):
    __tablename__ = "images"
    __table_args__ = (
        ForeignKeyConstraint(
            ["replacement_id"],
            ["images.image_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_images_replacement_id",
        ),
        ForeignKeyConstraint(
            ["status_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_images_status_user_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_images_user_id",
        ),
        Index("change_id", "change_id"),
        Index("fk_images_replacement_id", "replacement_id"),
        Index("fk_images_status_user_id", "status_user_id"),
        Index("fk_images_user_id", "user_id"),
        Index("idx_bayesian_rating", "bayesian_rating"),
        Index("idx_favorites", "favorites"),
        Index("idx_filename", "filename"),
        Index("idx_last_post", "last_post"),
        Index("idx_status", "status"),
        Index("idx_top_images", "num_ratings"),
        Index("idx_total_pixels", "total_pixels"),
    )

    image_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    user_id: Mapped[int] = mapped_column(INTEGER(10), nullable=False)
    useragent: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    ip: Mapped[str] = mapped_column(String(15), nullable=False, server_default=text("''"))
    status: Mapped[int] = mapped_column(TINYINT(2), nullable=False, server_default=text("1"))
    locked: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    ext: Mapped[str] = mapped_column(String(10), nullable=False)
    md5_hash: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("''"))
    filesize: Mapped[int] = mapped_column(INTEGER(9), nullable=False, server_default=text("0"))
    width: Mapped[int] = mapped_column(SMALLINT(6), nullable=False, server_default=text("0"))
    height: Mapped[int] = mapped_column(SMALLINT(6), nullable=False, server_default=text("0"))
    medium: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    large: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    posts: Mapped[int] = mapped_column(SMALLINT(4), nullable=False, server_default=text("0"))
    favorites: Mapped[int] = mapped_column(SMALLINT(4), nullable=False, server_default=text("0"))
    caption: Mapped[str] = mapped_column(String(35), nullable=False, server_default=text("''"))
    rating: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    bayesian_rating: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    num_ratings: Mapped[int] = mapped_column(INTEGER(4), nullable=False, server_default=text("0"))
    reviewed: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    change_id: Mapped[int] = mapped_column(INTEGER(10), nullable=False, server_default=text("0"))
    date_added: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, server_default=text("current_timestamp()")
    )
    status_user_id: Mapped[int | None] = mapped_column(INTEGER(10))
    status_updated: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    last_updated: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    last_post: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    filename: Mapped[str | None] = mapped_column(String(120))
    original_filename: Mapped[str | None] = mapped_column(String(120))
    total_pixels: Mapped[decimal.Decimal | None] = mapped_column(DECIMAL(6, 3))
    image_source: Mapped[str | None] = mapped_column(String(255))
    artist: Mapped[str | None] = mapped_column(String(200))
    characters: Mapped[str | None] = mapped_column(Text)
    miscmeta: Mapped[str | None] = mapped_column(String(255))
    replacement_id: Mapped[int | None] = mapped_column(INTEGER(10))

    replacement: Mapped[Optional["Images"]] = relationship(
        "Images", remote_side=[image_id], back_populates="replacement_reverse"
    )
    replacement_reverse: Mapped[list["Images"]] = relationship(
        "Images", remote_side=[replacement_id], back_populates="replacement"
    )
    status_user: Mapped[Optional["Users"]] = relationship(
        "Users", foreign_keys=[status_user_id], back_populates="images"
    )
    user: Mapped["Users"] = relationship("Users", foreign_keys=[user_id], back_populates="images_")
    users: Mapped[list["Users"]] = relationship(
        "Users", foreign_keys="[Users.bookmark]", back_populates="images1"
    )
    favorites_: Mapped[list["Favorites"]] = relationship("Favorites", back_populates="image")
    image_ratings: Mapped[list["ImageRatings"]] = relationship(
        "ImageRatings", back_populates="image"
    )
    image_reports: Mapped[list["ImageReports"]] = relationship(
        "ImageReports", back_populates="image"
    )
    image_reviews: Mapped[list["ImageReviews"]] = relationship(
        "ImageReviews", back_populates="image"
    )
    posts_: Mapped[list["Posts"]] = relationship("Posts", back_populates="image")
    tag_history: Mapped[list["TagHistory"]] = relationship("TagHistory", back_populates="image")
    tag_links: Mapped[list["TagLinks"]] = relationship("TagLinks", back_populates="image")


class Perms(Base):
    __tablename__ = "perms"

    perm_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(50))
    desc: Mapped[str | None] = mapped_column(String(75))

    group_perms: Mapped[list["GroupPerms"]] = relationship("GroupPerms", back_populates="perm")


class Tips(Base):
    __tablename__ = "tips"

    id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    type: Mapped[int] = mapped_column(INTEGER(1), nullable=False, server_default=text("0"))
    tip: Mapped[str | None] = mapped_column(String(255))


# class TwClosest(Base):
#     __tablename__ = 'tw_closest'
#     __table_args__ = (
#         Index('cluster', 'cluster'),
#         Index('tag_id', 'tag_id')
#     )

#     cl_id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
#     tag_id: Mapped[Optional[int]] = mapped_column(INTEGER(11))
#     cluster: Mapped[Optional[int]] = mapped_column(INTEGER(11))
#     dist: Mapped[Optional[int]] = mapped_column(INTEGER(11))
#     rtag_id: Mapped[Optional[int]] = mapped_column(INTEGER(11))


# class TwTagcluster(Base):
#     __tablename__ = 'tw_tagcluster'
#     __table_args__ = (
#         Index('cluster', 'cluster'),
#     )

#     tag_id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
#     cluster: Mapped[Optional[int]] = mapped_column(INTEGER(11))
#     main: Mapped[Optional[str]] = mapped_column(CHAR(1))
#     user_id: Mapped[Optional[int]] = mapped_column(INTEGER(10))


# class TwTaglink(Base):
#     __tablename__ = 'tw_taglink'
#     __table_args__ = (
#         Index('image_id', 'image_id'),
#         Index('tag_id', 'tag_id')
#     )

#     tag_link_id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
#     image_id: Mapped[Optional[int]] = mapped_column(INTEGER(11))
#     tag_id: Mapped[Optional[int]] = mapped_column(INTEGER(11))


# class TwTags(Base):
#     __tablename__ = 'tw_tags'
#     __table_args__ = (
#         Index('title', 'title'),
#     )

#     tag_id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
#     title: Mapped[Optional[str]] = mapped_column(VARCHAR(150))


class UserGroups(Base):
    __tablename__ = "user_groups"

    user_id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    group_id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)


class UserPerms(Base):
    __tablename__ = "user_perms"

    user_id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    perm_id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    permvalue: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("1"))


class Users(Base):
    __tablename__ = "users"
    __table_args__ = (
        ForeignKeyConstraint(
            ["bookmark"],
            ["images.image_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_bookmark",
        ),
        Index("fk_bookmark", "bookmark"),
        Index("username", "username", unique=True),
    )

    user_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    date_joined: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("current_timestamp()")
    )
    active: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    admin: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    username: Mapped[str] = mapped_column(String(30), nullable=False)
    password: Mapped[str] = mapped_column(String(40), nullable=False)
    salt: Mapped[str] = mapped_column(CHAR(16), nullable=False)
    timezone: Mapped[decimal.Decimal] = mapped_column(
        DECIMAL(5, 2), nullable=False, server_default=text("0.00")
    )
    email_pm_pref: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("1"))
    spoiler_warning_pref: Mapped[int] = mapped_column(
        TINYINT(1), nullable=False, server_default=text("1")
    )
    thumb_layout: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    sorting_pref: Mapped[str] = mapped_column(
        VARCHAR(100), nullable=False, server_default=text("'image_id'")
    )
    sorting_pref_order: Mapped[str] = mapped_column(
        VARCHAR(10), nullable=False, server_default=text("'DESC'")
    )
    images_per_page: Mapped[int] = mapped_column(
        INTEGER(3), nullable=False, server_default=text("10")
    )
    show_all_images: Mapped[int] = mapped_column(
        TINYINT(1), nullable=False, server_default=text("0")
    )
    show_all_meta: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    show_all_posts: Mapped[int] = mapped_column(
        TINYINT(1), nullable=False, server_default=text("0")
    )
    show_ip: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    posts: Mapped[int] = mapped_column(MEDIUMINT(8), nullable=False, server_default=text("0"))
    image_posts: Mapped[int] = mapped_column(MEDIUMINT(8), nullable=False, server_default=text("0"))
    favorites: Mapped[int] = mapped_column(INTEGER(10), nullable=False, server_default=text("0"))
    email: Mapped[str] = mapped_column(String(120), nullable=False)
    show_email: Mapped[int] = mapped_column(TINYINT(4), nullable=False, server_default=text("0"))
    avatar: Mapped[str] = mapped_column(VARCHAR(255), nullable=False, server_default=text("''"))
    avatar_type: Mapped[int] = mapped_column(TINYINT(2), nullable=False, server_default=text("0"))
    gender: Mapped[str] = mapped_column(CHAR(1), nullable=False, server_default=text("''"))
    actkey: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, server_default=text("''"))
    maximgperday: Mapped[int] = mapped_column(INTEGER(3), nullable=False, server_default=text("15"))
    rating_ratio: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    infected_by: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("0"))
    date_infected: Mapped[int] = mapped_column(
        INTEGER(11), nullable=False, server_default=text("0")
    )
    forum_id: Mapped[int | None] = mapped_column(MEDIUMINT(8))
    last_login: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, server_default=text("current_timestamp()")
    )
    bookmark: Mapped[int | None] = mapped_column(INTEGER(10))
    location: Mapped[str | None] = mapped_column(String(100))
    website: Mapped[str | None] = mapped_column(String(100))
    aim: Mapped[str | None] = mapped_column(String(50))
    interests: Mapped[str | None] = mapped_column(String(255))
    user_title: Mapped[str | None] = mapped_column(String(50))
    newpassword: Mapped[str | None] = mapped_column(String(40))
    newsalt: Mapped[str | None] = mapped_column(CHAR(16))
    infected: Mapped[int | None] = mapped_column(TINYINT(1), server_default=text("0"))
    last_login_new: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    images: Mapped[list["Images"]] = relationship(
        "Images", foreign_keys="[Images.status_user_id]", back_populates="status_user"
    )
    images_: Mapped[list["Images"]] = relationship(
        "Images", foreign_keys="[Images.user_id]", back_populates="user"
    )
    images1: Mapped[Optional["Images"]] = relationship(
        "Images", foreign_keys=[bookmark], back_populates="users"
    )
    bans: Mapped[list["Bans"]] = relationship(
        "Bans", foreign_keys="[Bans.banned_by]", back_populates="users"
    )
    bans_: Mapped[list["Bans"]] = relationship(
        "Bans", foreign_keys="[Bans.user_id]", back_populates="user"
    )
    favorites_: Mapped[list["Favorites"]] = relationship("Favorites", back_populates="user")
    image_ratings: Mapped[list["ImageRatings"]] = relationship(
        "ImageRatings", back_populates="user"
    )
    image_reports: Mapped[list["ImageReports"]] = relationship(
        "ImageReports", back_populates="user"
    )
    image_reviews: Mapped[list["ImageReviews"]] = relationship(
        "ImageReviews", back_populates="user"
    )
    news: Mapped[list["News"]] = relationship("News", back_populates="user")
    posts_: Mapped[list["Posts"]] = relationship(
        "Posts", foreign_keys="[Posts.last_updated_user_id]", back_populates="last_updated_user"
    )
    posts1: Mapped[list["Posts"]] = relationship(
        "Posts", foreign_keys="[Posts.user_id]", back_populates="user"
    )
    privmsgs: Mapped[list["Privmsgs"]] = relationship(
        "Privmsgs", foreign_keys="[Privmsgs.from_user_id]", back_populates="from_user"
    )
    privmsgs_: Mapped[list["Privmsgs"]] = relationship(
        "Privmsgs", foreign_keys="[Privmsgs.to_user_id]", back_populates="to_user"
    )
    quicklinks: Mapped[list["Quicklinks"]] = relationship("Quicklinks", back_populates="user")
    tags: Mapped[list["Tags"]] = relationship("Tags", back_populates="user")
    user_sessions: Mapped[list["UserSessions"]] = relationship(
        "UserSessions", back_populates="user"
    )
    tag_history: Mapped[list["TagHistory"]] = relationship("TagHistory", back_populates="user")
    tag_links: Mapped[list["TagLinks"]] = relationship("TagLinks", back_populates="user")


class Bans(Base):
    __tablename__ = "bans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["banned_by"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_bans_banned_by",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_bans_user_id",
        ),
        Index("fk_bans_banned_by", "banned_by"),
        Index("fk_bans_user_id", "user_id"),
    )

    ban_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    user_id: Mapped[int] = mapped_column(INTEGER(10), nullable=False)
    viewed: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    banned_by: Mapped[int | None] = mapped_column(INTEGER(10))
    ip: Mapped[str | None] = mapped_column(String(15))
    action: Mapped[str | None] = mapped_column(
        Enum("None", "One Week Ban", "Two Week Ban", "One Month Ban", "Permanent Ban")
    )
    reason: Mapped[str | None] = mapped_column(TINYTEXT)
    message: Mapped[str | None] = mapped_column(String(255))
    date: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, server_default=text("current_timestamp()")
    )
    expires: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    users: Mapped[Optional["Users"]] = relationship(
        "Users", foreign_keys=[banned_by], back_populates="bans"
    )
    user: Mapped["Users"] = relationship("Users", foreign_keys=[user_id], back_populates="bans_")


class Favorites(Base):
    __tablename__ = "favorites"
    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_favorites_image_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_favorites_user_id",
        ),
        Index("fk_favorites_image_id", "image_id"),
    )

    user_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    image_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    fav_date: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, server_default=text("current_timestamp()")
    )

    image: Mapped["Images"] = relationship("Images", back_populates="favorites_")
    user: Mapped["Users"] = relationship("Users", back_populates="favorites_")


class GroupPerms(Base):
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

    group_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    perm_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    permvalue: Mapped[int | None] = mapped_column(TINYINT(1))

    group: Mapped["Groups"] = relationship("Groups", back_populates="group_perms")
    perm: Mapped["Perms"] = relationship("Perms", back_populates="group_perms")


class ImageRatings(Base):
    __tablename__ = "image_ratings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_ratings_image_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_ratings_user_id",
        ),
        Index("fk_image_ratings_image_id", "image_id"),
    )

    user_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    image_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    rating: Mapped[int] = mapped_column(TINYINT(2), nullable=False, server_default=text("0"))
    date: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, server_default=text("current_timestamp()")
    )

    image: Mapped["Images"] = relationship("Images", back_populates="image_ratings")
    user: Mapped["Users"] = relationship("Users", back_populates="image_ratings")


class ImageReports(Base):
    __tablename__ = "image_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_reports_image_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_reports_user_id",
        ),
        Index("fk_image_reports_image_id", "image_id"),
        Index("fk_image_reports_user_id", "user_id"),
        Index("open", "open"),
    )

    image_report_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    image_id: Mapped[int] = mapped_column(INTEGER(10), nullable=False)
    user_id: Mapped[int] = mapped_column(INTEGER(10), nullable=False)
    open: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("1"))
    category: Mapped[int | None] = mapped_column(TINYINT(3))
    text_: Mapped[str | None] = mapped_column("text", Text)
    date: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, server_default=text("current_timestamp()")
    )

    image: Mapped["Images"] = relationship("Images", back_populates="image_reports")
    user: Mapped["Users"] = relationship("Users", back_populates="image_reports")


class ImageReviews(Base):
    __tablename__ = "image_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_reviews_image_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_image_reviews_user_id",
        ),
        Index("fk_image_reviews_user_id", "user_id"),
        Index("image_id", "image_id", "user_id", unique=True),
    )

    image_review_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    image_id: Mapped[int | None] = mapped_column(INTEGER(10))
    user_id: Mapped[int | None] = mapped_column(INTEGER(10))
    vote: Mapped[int | None] = mapped_column(TINYINT(1))

    image: Mapped[Optional["Images"]] = relationship("Images", back_populates="image_reviews")
    user: Mapped[Optional["Users"]] = relationship("Users", back_populates="image_reviews")


class News(Base):
    __tablename__ = "news"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_news_user_id",
        ),
        Index("fk_news_user_id", "user_id"),
    )

    news_id: Mapped[int] = mapped_column(SMALLINT(8), primary_key=True)
    user_id: Mapped[int] = mapped_column(INTEGER(10), nullable=False)
    title: Mapped[str | None] = mapped_column(String(128))
    news_text: Mapped[str | None] = mapped_column(Text)
    date: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, server_default=text("current_timestamp()")
    )
    edited: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    user: Mapped["Users"] = relationship("Users", back_populates="news")


class Posts(Base):
    __tablename__ = "posts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_posts_image_id",
        ),
        ForeignKeyConstraint(
            ["last_updated_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_posts_last_updated_user_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_posts_user_id",
        ),
        Index("fk_posts_image_id", "image_id"),
        Index("fk_posts_last_updated_user_id", "last_updated_user_id"),
        Index("fk_posts_user_id", "user_id"),
        Index("idx_date", "date"),
    )

    post_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    user_id: Mapped[int] = mapped_column(INTEGER(10), nullable=False)
    useragent: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    ip: Mapped[str] = mapped_column(String(15), nullable=False, server_default=text("''"))
    date: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("current_timestamp()")
    )
    update_count: Mapped[int] = mapped_column(INTEGER(3), nullable=False, server_default=text("0"))
    post_text: Mapped[str] = mapped_column(Text, nullable=False)
    image_id: Mapped[int | None] = mapped_column(INTEGER(10))
    last_updated: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    last_updated_user_id: Mapped[int | None] = mapped_column(INTEGER(10))

    image: Mapped[Optional["Images"]] = relationship("Images", back_populates="posts_")
    last_updated_user: Mapped[Optional["Users"]] = relationship(
        "Users", foreign_keys=[last_updated_user_id], back_populates="posts_"
    )
    user: Mapped["Users"] = relationship("Users", foreign_keys=[user_id], back_populates="posts1")


class Privmsgs(Base):
    __tablename__ = "privmsgs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["from_user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_privmsgs_from_user_id",
        ),
        ForeignKeyConstraint(
            ["to_user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_privmsgs_to_user_id",
        ),
        Index("fk_privmsgs_from_user_id", "from_user_id"),
        Index("fk_privmsgs_to_user_id", "to_user_id"),
    )

    privmsg_id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    from_user_id: Mapped[int] = mapped_column(INTEGER(10), nullable=False)
    to_user_id: Mapped[int] = mapped_column(INTEGER(10), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    viewed: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    from_del: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    to_del: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    type: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("1"))
    card: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("0"))
    cardpath: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    text_: Mapped[str | None] = mapped_column("text", Text)
    date: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, server_default=text("current_timestamp()")
    )

    from_user: Mapped["Users"] = relationship(
        "Users", foreign_keys=[from_user_id], back_populates="privmsgs"
    )
    to_user: Mapped["Users"] = relationship(
        "Users", foreign_keys=[to_user_id], back_populates="privmsgs_"
    )


class Quicklinks(Base):
    __tablename__ = "quicklinks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_quicklinks_user_id",
        ),
        Index("fk_quicklinks_user_id", "user_id"),
    )

    quicklink_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(INTEGER(10))
    link: Mapped[str | None] = mapped_column(String(32))

    user: Mapped[Optional["Users"]] = relationship("Users", back_populates="quicklinks")


class Tags(Base):
    __tablename__ = "tags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["alias"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tags_alias",
        ),
        ForeignKeyConstraint(
            ["inheritedfrom_id"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tags_inheritedfrom_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tags_user_id",
        ),
        Index("fk_tags_alias", "alias"),
        Index("fk_tags_inheritedfrom_id", "inheritedfrom_id"),
        Index("fk_tags_user_id", "user_id"),
        Index("type_alias", "type", "alias"),
    )

    tag_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    date_added: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("current_timestamp()")
    )
    type: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default=text("1"))
    title: Mapped[str | None] = mapped_column(String(150))
    desc: Mapped[str | None] = mapped_column(String(200))
    alias: Mapped[int | None] = mapped_column(INTEGER(10))
    inheritedfrom_id: Mapped[int | None] = mapped_column(INTEGER(10))
    user_id: Mapped[int | None] = mapped_column(INTEGER(10))
    # tw_tagid: Mapped[Optional[int]] = mapped_column(INTEGER(11))

    tags: Mapped[Optional["Tags"]] = relationship(
        "Tags", remote_side=[tag_id], foreign_keys=[alias], back_populates="tags_reverse"
    )
    tags_reverse: Mapped[list["Tags"]] = relationship(
        "Tags", remote_side=[alias], foreign_keys=[alias], back_populates="tags"
    )
    inheritedfrom: Mapped[Optional["Tags"]] = relationship(
        "Tags",
        remote_side=[tag_id],
        foreign_keys=[inheritedfrom_id],
        back_populates="inheritedfrom_reverse",
    )
    inheritedfrom_reverse: Mapped[list["Tags"]] = relationship(
        "Tags",
        remote_side=[inheritedfrom_id],
        foreign_keys=[inheritedfrom_id],
        back_populates="inheritedfrom",
    )
    user: Mapped[Optional["Users"]] = relationship("Users", back_populates="tags")
    tag_history: Mapped[list["TagHistory"]] = relationship("TagHistory", back_populates="tag")
    tag_links: Mapped[list["TagLinks"]] = relationship("TagLinks", back_populates="tag")


class UserSessions(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_user_sessions_user_id",
        ),
        Index("fk_user_sessions_user_id", "user_id"),
        Index("ip", "ip"),
    )

    session_id: Mapped[str] = mapped_column(String(50), primary_key=True, server_default=text("''"))
    user_id: Mapped[int] = mapped_column(INTEGER(10), nullable=False)
    last_used: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("current_timestamp()")
    )
    ip: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("''"))
    last_view_date: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, server_default=text("current_timestamp()")
    )
    lastpage: Mapped[str | None] = mapped_column(String(200))
    last_search: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, server_default=text("current_timestamp()")
    )

    user: Mapped["Users"] = relationship("Users", back_populates="user_sessions")


class TagHistory(Base):
    __tablename__ = "tag_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_history_image_id",
        ),
        ForeignKeyConstraint(
            ["tag_id"],
            ["tags.tag_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_history_tag_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_history_user_id",
        ),
        Index("fk_tag_history_tag_id", "tag_id"),
        Index("image_id", "image_id"),
        Index("user_id", "user_id"),
    )

    tag_history_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    image_id: Mapped[int | None] = mapped_column(INTEGER(10))
    tag_id: Mapped[int | None] = mapped_column(INTEGER(10))
    user_id: Mapped[int | None] = mapped_column(INTEGER(10))
    action: Mapped[str | None] = mapped_column(CHAR(1))
    date: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, server_default=text("current_timestamp()")
    )

    image: Mapped[Optional["Images"]] = relationship("Images", back_populates="tag_history")
    tag: Mapped[Optional["Tags"]] = relationship("Tags", back_populates="tag_history")
    user: Mapped[Optional["Users"]] = relationship("Users", back_populates="tag_history")


class TagLinks(Base):
    __tablename__ = "tag_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["image_id"],
            ["images.image_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_tag_links_image_id",
        ),
        ForeignKeyConstraint(
            ["tag_id"],
            ["tags.tag_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_tag_links_tag_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_tag_links_user_id",
        ),
        Index("fk_tag_links_image_id", "image_id"),
        Index("fk_tag_links_user_id", "user_id"),
    )

    tag_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    image_id: Mapped[int] = mapped_column(INTEGER(10), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(INTEGER(10))
    date_linked: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, server_default=text("current_timestamp()")
    )

    image: Mapped["Images"] = relationship("Images", back_populates="tag_links")
    tag: Mapped["Tags"] = relationship("Tags", back_populates="tag_links")
    user: Mapped[Optional["Users"]] = relationship("Users", back_populates="tag_links")
