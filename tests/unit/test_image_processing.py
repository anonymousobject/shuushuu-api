"""
Tests for image processing utilities.
"""

import tempfile
from pathlib import Path

import pytest
from PIL import Image

from app.config import settings
from app.services.image_processing import create_large_variant, create_medium_variant


@pytest.fixture
def test_image_large():
    """Create a temporary large test image (3000x2000 pixels)."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        img = Image.new("RGB", (3000, 2000), color="red")
        img.save(f.name, quality=95)
        yield Path(f.name)
        # Cleanup
        Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def test_image_medium():
    """Create a temporary medium test image (1500x1000 pixels)."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        img = Image.new("RGB", (1500, 1000), color="blue")
        img.save(f.name, quality=95)
        yield Path(f.name)
        # Cleanup
        Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def test_image_small():
    """Create a temporary small test image (800x600 pixels)."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        img = Image.new("RGB", (800, 600), color="green")
        img.save(f.name, quality=95)
        yield Path(f.name)
        # Cleanup
        Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def temp_storage():
    """Create a temporary storage directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def test_image_highly_compressed():
    """Create a temporary highly compressed small image for size validation tests."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        # Create a small 100x100 solid color image with very low quality
        # This will be tiny and resizing won't make it smaller
        img = Image.new("RGB", (100, 100), color="red")
        img.save(f.name, quality=1)  # Extremely low quality
        yield Path(f.name)
        # Cleanup
        Path(f.name).unlink(missing_ok=True)


class TestCreateMediumVariant:
    """Tests for create_medium_variant function."""

    def test_creates_medium_variant_when_image_exceeds_threshold(
        self, test_image_large, temp_storage
    ):
        """Test that medium variant is created when image exceeds MEDIUM_EDGE."""
        image_id = 123
        ext = "jpg"
        width = 3000
        height = 2000

        result = create_medium_variant(
            source_path=test_image_large,
            image_id=image_id,
            ext=ext,
            storage_path=temp_storage,
            width=width,
            height=height,
        )

        # Should return True (variant created)
        assert result is True

        # Check that medium directory was created
        medium_dir = Path(temp_storage) / "medium"
        assert medium_dir.exists()

        # Check that variant file exists with correct naming
        from datetime import datetime

        date_prefix = datetime.now().strftime("%Y-%m-%d")
        expected_filename = f"{date_prefix}-{image_id}.{ext}"
        variant_path = medium_dir / expected_filename
        assert variant_path.exists()

        # Verify dimensions are within bounds
        with Image.open(variant_path) as img:
            assert img.width <= settings.MEDIUM_EDGE
            assert img.height <= settings.MEDIUM_EDGE
            # Check aspect ratio maintained (3000:2000 = 1.5:1)
            assert abs(img.width / img.height - 1.5) < 0.01

    def test_does_not_create_medium_variant_when_image_below_threshold(
        self, test_image_small, temp_storage
    ):
        """Test that medium variant is NOT created when image is smaller than MEDIUM_EDGE."""
        image_id = 124
        ext = "jpg"
        width = 800
        height = 600

        result = create_medium_variant(
            source_path=test_image_small,
            image_id=image_id,
            ext=ext,
            storage_path=temp_storage,
            width=width,
            height=height,
        )

        # Should return False (variant not created)
        assert result is False

        # Check that medium directory was not created
        medium_dir = Path(temp_storage) / "medium"
        assert not medium_dir.exists()


class TestCreateLargeVariant:
    """Tests for create_large_variant function."""

    def test_creates_large_variant_when_image_exceeds_threshold(
        self, test_image_large, temp_storage
    ):
        """Test that large variant is created when image exceeds LARGE_EDGE."""
        image_id = 125
        ext = "jpg"
        width = 3000
        height = 2000

        result = create_large_variant(
            source_path=test_image_large,
            image_id=image_id,
            ext=ext,
            storage_path=temp_storage,
            width=width,
            height=height,
        )

        # Should return True (variant created)
        assert result is True

        # Check that large directory was created
        large_dir = Path(temp_storage) / "large"
        assert large_dir.exists()

        # Check that variant file exists with correct naming
        from datetime import datetime

        date_prefix = datetime.now().strftime("%Y-%m-%d")
        expected_filename = f"{date_prefix}-{image_id}.{ext}"
        variant_path = large_dir / expected_filename
        assert variant_path.exists()

        # Verify dimensions are within bounds
        with Image.open(variant_path) as img:
            assert img.width <= settings.LARGE_EDGE
            assert img.height <= settings.LARGE_EDGE
            # Check aspect ratio maintained (3000:2000 = 1.5:1)
            assert abs(img.width / img.height - 1.5) < 0.01

    def test_does_not_create_large_variant_when_image_below_threshold(
        self, test_image_medium, temp_storage
    ):
        """Test that large variant is NOT created when image is smaller than LARGE_EDGE."""
        image_id = 126
        ext = "jpg"
        width = 1500
        height = 1000

        result = create_large_variant(
            source_path=test_image_medium,
            image_id=image_id,
            ext=ext,
            storage_path=temp_storage,
            width=width,
            height=height,
        )

        # Should return False (variant not created)
        assert result is False

        # Check that large directory was not created
        large_dir = Path(temp_storage) / "large"
        assert not large_dir.exists()


class TestFileSizeValidation:
    """Tests for file size validation in variant creation."""

    def test_medium_variant_deleted_when_larger_than_original(self, temp_storage):
        """Test that medium variant is deleted if it's larger than the original."""
        # Create an already-optimized tiny image that will produce a LARGER resized version
        # Use a very small JPEG with maximum compression
        original_path = Path(temp_storage) / "original.jpg"
        # Tiny image with extreme compression
        img = Image.new("RGB", (1500, 1000), color=(255, 0, 0))
        img.save(original_path, format="JPEG", quality=1, optimize=True)

        original_size = original_path.stat().st_size

        image_id = 999
        ext = "jpg"
        width = 1500
        height = 1000

        result = create_medium_variant(
            source_path=original_path,
            image_id=image_id,
            ext=ext,
            storage_path=temp_storage,
            width=width,
            height=height,
        )

        # Get variant path
        from datetime import datetime

        medium_dir = Path(temp_storage) / "medium"
        date_prefix = datetime.now().strftime("%Y-%m-%d")
        expected_filename = f"{date_prefix}-{image_id}.{ext}"
        variant_path = medium_dir / expected_filename

        # If variant was created, check if it was kept based on size
        if variant_path.exists():
            variant_size = variant_path.stat().st_size
            # Variant should only exist if it's smaller than original
            assert variant_size < original_size, (
                f"Variant ({variant_size} bytes) should be deleted when larger than "
                f"original ({original_size} bytes), but it still exists"
            )
            assert result is True
        else:
            # Variant was deleted or not created
            # In this case, result should be False
            assert result is False

    def test_large_variant_deleted_when_larger_than_original(self, temp_storage):
        """Test that large variant is deleted if it's larger than the original."""
        # Create an already-optimized tiny image that will produce a LARGER resized version
        original_path = Path(temp_storage) / "original.jpg"
        # Tiny image with extreme compression
        img = Image.new("RGB", (3000, 2000), color=(0, 255, 0))
        img.save(original_path, format="JPEG", quality=1, optimize=True)

        original_size = original_path.stat().st_size

        image_id = 1000
        ext = "jpg"
        width = 3000
        height = 2000

        result = create_large_variant(
            source_path=original_path,
            image_id=image_id,
            ext=ext,
            storage_path=temp_storage,
            width=width,
            height=height,
        )

        # Get variant path
        from datetime import datetime

        large_dir = Path(temp_storage) / "large"
        date_prefix = datetime.now().strftime("%Y-%m-%d")
        expected_filename = f"{date_prefix}-{image_id}.{ext}"
        variant_path = large_dir / expected_filename

        # If variant was created, check if it was kept based on size
        if variant_path.exists():
            variant_size = variant_path.stat().st_size
            # Variant should only exist if it's smaller than original
            assert variant_size < original_size, (
                f"Variant ({variant_size} bytes) should be deleted when larger than "
                f"original ({original_size} bytes), but it still exists"
            )
            assert result is True
        else:
            # Variant was deleted or not created
            # In this case, result should be False
            assert result is False
