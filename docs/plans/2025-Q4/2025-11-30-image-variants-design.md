# Image Variant Generation Design

**Date:** 2025-11-30
**Status:** Approved

## Overview

Implement the `create_medium_variant()` and `create_large_variant()` functions to generate resized image variants during upload, following the same pattern as the existing thumbnail generation.

## Storage Structure

- **Medium variants**: `{storage_path}/medium/YYYY-MM-DD-{image_id}.{ext}`
- **Large variants**: `{storage_path}/large/YYYY-MM-DD-{image_id}.{ext}`
- Both directories created with `mkdir(parents=True, exist_ok=True)`

## Resize Logic

- **Medium variant**: Created when `width > MEDIUM_EDGE (1280)` OR `height > MEDIUM_EDGE`
- **Large variant**: Created when `width > LARGE_EDGE (2048)` OR `height > LARGE_EDGE`
- Uses `PIL.Image.thumbnail((size, size))` to fit within bounding box while maintaining aspect ratio
- Resampling method: `Image.Resampling.LANCZOS` (high-quality downsampling, same as thumbnails)

## Image Format Handling

- **RGBA to RGB conversion**: Applied for JPEG formats (same as thumbnails)
  - Creates white background
  - Pastes image with alpha mask
- **Quality settings**: Uses `settings.LARGE_QUALITY (90)` for both medium and large variants
- **Optimization**: Applies `optimize=True` for JPEG, quality setting for WebP

## File Size Validation

### Size Check Logic

After generating each variant:

1. Compare variant file size to original file size
2. **If variant ≥ original size**:
   - Delete the variant file
   - Update database record: set `has_medium` or `has_large` to `0`
   - Return `False` (variant not created)
3. **If variant < original size**:
   - Keep the variant
   - Return `True` (variant successfully created)

### Why This Matters

- Some images compress poorly when resized (e.g., already heavily optimized PNGs)
- No point serving a "smaller" variant that's actually larger
- Database fields must accurately reflect whether usable variants exist

### Database Update Implementation

Since variants are background tasks that run after the database insert:
- Background task updates the database record if size check fails
- Import necessary models/database session in the background task
- Handle potential database errors during update (log but don't crash)

## Error Handling and Logging

Following the thumbnail pattern:

### Logging

- **Start**: Log variant generation started with source path
- **Success**: Log variant created with:
  - Variant path
  - Original dimensions
  - Resized dimensions
  - File sizes (original vs variant)
- **Size check failure**: Log that variant was larger than original, file deleted
- **Exception**: Log error with error type and message (silent failure)

### Context Binding

- Bind task context: `task="medium_variant_generation"` or `task="large_variant_generation"`
- Include `image_id` in context for tracing

### Silent Failure

- All exceptions caught and logged
- Background task doesn't crash or block upload
- Same pattern as thumbnail generation

## Implementation Files

- `app/services/image_processing.py`: Implement `create_medium_variant()` and `create_large_variant()`
- `app/api/v1/images.py`: Already calling these functions as background tasks (lines 754-776)
- `app/config.py`: Configuration already present (MEDIUM_EDGE, LARGE_EDGE, LARGE_QUALITY)
