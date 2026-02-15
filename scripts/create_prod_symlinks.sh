#!/usr/bin/env bash
set -euo pipefail

# Create production symlink tree for image files
# Links the new directory structure to existing PHP site image files
#
# Usage:
#   SRC_BASE=/path/to/php/images ./scripts/create_prod_symlinks.sh           # Dry-run (preview)
#   SRC_BASE=/path/to/php/images ./scripts/create_prod_symlinks.sh --apply   # Create symlinks
#
# Environment variables:
#   SRC_BASE   - (required) Path to the PHP site's fullsize image directory
#   DEST_BASE  - (optional) Destination directory, defaults to /shuushuu/images

DEST_BASE="${DEST_BASE:-/shuushuu/images}"
DRY_RUN=1

if [ "${1:-}" = "--apply" ]; then
    DRY_RUN=0
fi

# Validate SRC_BASE is set and exists
if [ -z "${SRC_BASE:-}" ]; then
    echo "ERROR: SRC_BASE must be set to the PHP site's image directory" >&2
    echo "Usage: SRC_BASE=/path/to/images $0 [--apply]" >&2
    exit 1
fi

if [ ! -d "$SRC_BASE" ]; then
    echo "ERROR: SRC_BASE directory does not exist: $SRC_BASE" >&2
    exit 1
fi

echo "=== Production Image Symlink Creator ==="
echo "Source:      $SRC_BASE"
echo "Destination: $DEST_BASE"
echo "Mode:        $([ "$DRY_RUN" -eq 1 ] && echo 'DRY RUN (use --apply to create symlinks)' || echo 'APPLY')"
echo ""

# Create destination directories
dirs=(fullsize medium large avatars banners)
if [ "$DRY_RUN" -eq 0 ]; then
    for dir in "${dirs[@]}"; do
        mkdir -p "$DEST_BASE/$dir"
    done
fi

# Helper: create a symlink (or print what would be done in dry-run mode)
# Arguments: $1 = source file (absolute path), $2 = destination symlink path
create_link() {
    local src="$1" dst="$2"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  LINK: $dst -> $src"
    else
        # Skip if symlink already exists and points to the same target
        if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
            return
        fi
        # Refuse to overwrite real files (only replace symlinks)
        if [ -e "$dst" ] && [ ! -L "$dst" ]; then
            echo "  WARNING: Skipping $dst — real file exists (not a symlink). Use rm manually if intended." >&2
            return
        fi
        # Remove existing symlink at destination before creating
        rm -f "$dst"
        ln -s "$src" "$dst"
    fi
}

# ---------------------------------------------------------------------------
# 1) Fullsize: all image files excluding medium, large, and thumb variants
# ---------------------------------------------------------------------------
echo "--- Fullsize ---"
fullsize_count=0

# Process main directory (non-recursive for the top level, but find handles it)
while IFS= read -r -d '' f; do
    base=$(basename "$f")
    create_link "$f" "$DEST_BASE/fullsize/$base"
    fullsize_count=$((fullsize_count + 1))
done < <(find "$SRC_BASE" -maxdepth 1 -type f \
    \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.gif' -o -iname '*.webp' \) \
    ! -iname '*medium*' ! -iname '*large*' ! -iname '*thumb*' \
    -print0)

# Flatten deactivated subdirectory into fullsize (if it exists)
if [ -d "$SRC_BASE/deactivated" ]; then
    echo "  (including deactivated/ images)"
    while IFS= read -r -d '' f; do
        base=$(basename "$f")
        create_link "$f" "$DEST_BASE/fullsize/$base"
        fullsize_count=$((fullsize_count + 1))
    done < <(find "$SRC_BASE/deactivated" -type f \
        \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.gif' -o -iname '*.webp' \) \
        ! -iname '*medium*' ! -iname '*large*' ! -iname '*thumb*' \
        -print0)
fi

echo "  Count: $fullsize_count"
echo ""

# ---------------------------------------------------------------------------
# 2) Medium: files with 'medium' in the name, strip -medium suffix
# ---------------------------------------------------------------------------
echo "--- Medium ---"
medium_count=0

while IFS= read -r -d '' f; do
    base=$(basename "$f")
    # Strip -medium from the filename, e.g. 123-medium.jpg -> 123.jpg
    newname=$(echo "$base" | sed -E 's/-medium([.][^.]+)$/\1/I')
    create_link "$f" "$DEST_BASE/medium/$newname"
    medium_count=$((medium_count + 1))
done < <(find "$SRC_BASE" -type f -iname '*medium*' \
    \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.gif' -o -iname '*.webp' \) \
    -print0)

echo "  Count: $medium_count"
echo ""

# ---------------------------------------------------------------------------
# 3) Large: files with 'large' in the name, strip -large suffix
# ---------------------------------------------------------------------------
echo "--- Large ---"
large_count=0

while IFS= read -r -d '' f; do
    base=$(basename "$f")
    # Strip -large from the filename, e.g. 123-large.jpg -> 123.jpg
    newname=$(echo "$base" | sed -E 's/-large([.][^.]+)$/\1/I')
    create_link "$f" "$DEST_BASE/large/$newname"
    large_count=$((large_count + 1))
done < <(find "$SRC_BASE" -type f -iname '*large*' \
    \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.gif' -o -iname '*.webp' \) \
    -print0)

echo "  Count: $large_count"
echo ""

# ---------------------------------------------------------------------------
# 4) Avatars: symlink avatar files if the directory exists
# ---------------------------------------------------------------------------
echo "--- Avatars ---"
avatar_count=0

if [ -d "$SRC_BASE/avatars" ]; then
    while IFS= read -r -d '' f; do
        base=$(basename "$f")
        create_link "$f" "$DEST_BASE/avatars/$base"
        avatar_count=$((avatar_count + 1))
    done < <(find "$SRC_BASE/avatars" -type f \
        \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.gif' -o -iname '*.webp' \) \
        -print0)
    echo "  Count: $avatar_count"
else
    echo "  (skipped - $SRC_BASE/avatars does not exist)"
fi
echo ""

# ---------------------------------------------------------------------------
# 5) Banners: symlink banner files if the directory exists
# ---------------------------------------------------------------------------
echo "--- Banners ---"
banner_count=0

if [ -d "$SRC_BASE/banners" ]; then
    while IFS= read -r -d '' f; do
        base=$(basename "$f")
        create_link "$f" "$DEST_BASE/banners/$base"
        banner_count=$((banner_count + 1))
    done < <(find "$SRC_BASE/banners" -type f \
        \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.gif' -o -iname '*.webp' \) \
        -print0)
    echo "  Count: $banner_count"
else
    echo "  (skipped - $SRC_BASE/banners does not exist)"
fi
echo ""

# ---------------------------------------------------------------------------
# Note: Thumbs are skipped — thumbnails are generated as WebP on demand.
# ---------------------------------------------------------------------------

# Summary
total=$((fullsize_count + medium_count + large_count + avatar_count + banner_count))
echo "=== Summary ==="
echo "  Fullsize: $fullsize_count"
echo "  Medium:   $medium_count"
echo "  Large:    $large_count"
echo "  Avatars:  $avatar_count"
echo "  Banners:  $banner_count"
echo "  Total:    $total"
echo ""

if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY RUN complete. Re-run with --apply to create symlinks."
else
    echo "Symlinks created successfully."
fi
