"""Test for UserBase.avatar_in_r2 field."""


def test_userbase_has_avatar_in_r2_field():
    from app.models.user import UserBase

    field = UserBase.model_fields["avatar_in_r2"]
    assert field.annotation is bool
    assert field.default is False
