"""
Unit tests for the image reporting and review system.

Tests status constants, model defaults, and validation.
"""

from app.config import (
    AdminActionType,
    ImageStatus,
    ReportStatus,
    ReviewOutcome,
    ReviewStatus,
    ReviewType,
)


class TestStatusConstants:
    """Tests for status constant values."""

    def test_image_status_low_quality_exists(self):
        """Verify ImageStatus.LOW_QUALITY exists with correct value."""
        assert hasattr(ImageStatus, "LOW_QUALITY")
        assert ImageStatus.LOW_QUALITY == -3

    def test_report_status_values(self):
        """Verify ReportStatus has expected values."""
        assert ReportStatus.PENDING == 0
        assert ReportStatus.REVIEWED == 1
        assert ReportStatus.DISMISSED == 2

    def test_review_status_values(self):
        """Verify ReviewStatus has expected values."""
        assert ReviewStatus.OPEN == 0
        assert ReviewStatus.CLOSED == 1

    def test_review_outcome_values(self):
        """Verify ReviewOutcome has expected values."""
        assert ReviewOutcome.PENDING == 0
        assert ReviewOutcome.KEEP == 1
        assert ReviewOutcome.REMOVE == 2

    def test_review_type_values(self):
        """Verify ReviewType has expected values."""
        assert ReviewType.APPROPRIATENESS == 1

    def test_admin_action_type_values(self):
        """Verify AdminActionType has expected values."""
        assert AdminActionType.REPORT_DISMISS == 1
        assert AdminActionType.REPORT_ACTION == 2
        assert AdminActionType.REVIEW_START == 3
        assert AdminActionType.REVIEW_VOTE == 4
        assert AdminActionType.REVIEW_CLOSE == 5
        assert AdminActionType.REVIEW_EXTEND == 6


class TestModelDefaults:
    """Tests for model default values."""

    def test_image_report_defaults(self):
        """Verify ImageReports has correct default values."""
        from app.models.image_report import ImageReports

        report = ImageReports(image_id=1, user_id=1, category=1)
        assert report.status == ReportStatus.PENDING
        assert report.reason_text is None
        assert report.reviewed_by is None
        assert report.reviewed_at is None

    def test_image_review_defaults(self):
        """Verify ImageReviews has correct default values."""
        from app.models.image_review import ImageReviews

        review = ImageReviews(image_id=1)
        assert review.status == ReviewStatus.OPEN
        assert review.outcome == ReviewOutcome.PENDING
        assert review.extension_used == 0
        assert review.review_type == ReviewType.APPROPRIATENESS
        assert review.source_report_id is None

    def test_review_vote_defaults(self):
        """Verify ReviewVotes has correct default values."""
        from app.models.review_vote import ReviewVotes

        vote = ReviewVotes(user_id=1, vote=1)
        assert vote.review_id is None
        assert vote.image_id is None
        assert vote.comment is None

    def test_admin_action_accepts_dict_details(self):
        """Verify AdminActions accepts dict for details field."""
        from app.models.admin_action import AdminActions

        action = AdminActions(
            user_id=1,
            action_type=AdminActionType.REPORT_DISMISS,
            details={"previous_status": 1, "new_status": -2},
        )
        assert action.details == {"previous_status": 1, "new_status": -2}

    def test_admin_action_accepts_empty_details(self):
        """Verify AdminActions accepts empty dict for details."""
        from app.models.admin_action import AdminActions

        action = AdminActions(
            user_id=1,
            action_type=AdminActionType.REPORT_DISMISS,
            details={},
        )
        assert action.details == {}

    def test_admin_action_accepts_none_details(self):
        """Verify AdminActions accepts None for details."""
        from app.models.admin_action import AdminActions

        action = AdminActions(
            user_id=1,
            action_type=AdminActionType.REPORT_DISMISS,
        )
        assert action.details is None


class TestSchemaValidation:
    """Tests for Pydantic schema validation."""

    def test_report_create_schema(self):
        """Verify ReportCreate schema validation."""
        from app.schemas.report import ReportCreate

        # Valid data
        report = ReportCreate(category=1, reason_text="Test reason")
        assert report.category == 1
        assert report.reason_text == "Test reason"

        # Category only
        report = ReportCreate(category=2)
        assert report.category == 2
        assert report.reason_text is None

    def test_review_vote_request_schema(self):
        """Verify ReviewVoteRequest schema validation."""
        from app.schemas.report import ReviewVoteRequest

        # Keep vote
        vote = ReviewVoteRequest(vote=1, comment="Looks fine")
        assert vote.vote == 1
        assert vote.comment == "Looks fine"

        # Remove vote without comment
        vote = ReviewVoteRequest(vote=0)
        assert vote.vote == 0
        assert vote.comment is None

    def test_review_create_schema(self):
        """Verify ReviewCreate schema validation."""
        from app.schemas.report import ReviewCreate

        # Default deadline
        review = ReviewCreate()
        assert review.deadline_days is None

        # Custom deadline
        review = ReviewCreate(deadline_days=14)
        assert review.deadline_days == 14

    def test_report_response_labels(self):
        """Verify ReportResponse computes correct labels."""
        from app.schemas.report import ReportResponse

        response = ReportResponse(
            report_id=1,
            image_id=1,
            user_id=1,
            category=2,  # Inappropriate
            reason_text=None,
            status=0,  # Pending
            created_at=None,
            reviewed_by=None,
            reviewed_at=None,
        )
        assert response.category_label == "Inappropriate Image"
        assert response.status_label == "Pending"

    def test_review_response_labels(self):
        """Verify ReviewResponse computes correct labels."""
        from app.schemas.report import ReviewResponse

        response = ReviewResponse(
            review_id=1,
            image_id=1,
            source_report_id=None,
            initiated_by=1,
            review_type=1,  # Appropriateness
            deadline=None,
            extension_used=0,
            status=0,  # Open
            outcome=0,  # Pending
            created_at=None,
            closed_at=None,
        )
        assert response.review_type_label == "Appropriateness"
        assert response.status_label == "Open"
        assert response.outcome_label == "Pending"

    def test_vote_response_labels(self):
        """Verify VoteResponse computes correct labels."""
        from app.schemas.report import VoteResponse

        # Keep vote
        response = VoteResponse(
            vote_id=1,
            review_id=1,
            user_id=1,
            vote=1,
            comment=None,
            created_at=None,
        )
        assert response.vote_label == "Keep"

        # Remove vote
        response = VoteResponse(
            vote_id=2,
            review_id=1,
            user_id=2,
            vote=0,
            comment=None,
            created_at=None,
        )
        assert response.vote_label == "Remove"
