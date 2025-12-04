#!/usr/bin/env python3
"""
Analyze tag distribution in the database for ML training feasibility.

This script provides insights into:
- Total unique tags
- Tag frequency distribution
- Tags per image statistics
- Most/least common tags
- Tag type distribution

Usage:
    python scripts/analyze_tag_distribution.py
    # or with uv:
    uv run python scripts/analyze_tag_distribution.py
"""

import sys
from pathlib import Path

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from collections import Counter
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from app.config import settings, TagType
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.image import Images


def analyze_tag_distribution():
    """Analyze tag distribution for ML training assessment."""

    # Create sync engine for this analysis script
    engine = create_engine(settings.DATABASE_URL_SYNC, echo=False)
    Session = sessionmaker(bind=engine)

    print("=" * 80)
    print("TAG DISTRIBUTION ANALYSIS FOR ML TRAINING")
    print("=" * 80)
    print()

    with Session() as session:
        # 1. Total counts
        print("📊 OVERALL STATISTICS")
        print("-" * 80)

        total_images = session.scalar(select(func.count(Images.image_id)))
        total_tags = session.scalar(select(func.count(Tags.tag_id)))
        total_tag_links = session.scalar(select(func.count(TagLinks.tag_id)))

        print(f"Total images:           {total_images:,}")
        print(f"Total unique tags:      {total_tags:,}")
        print(f"Total tag applications: {total_tag_links:,}")
        print(f"Avg tags per image:     {total_tag_links / total_images:.2f}")
        print()

        # 2. Tag type distribution
        print("📑 TAG TYPE DISTRIBUTION")
        print("-" * 80)

        tag_type_counts = session.execute(
            select(Tags.type, func.count(Tags.tag_id))
            .group_by(Tags.type)
        ).all()

        type_names = {
            TagType.ALL: "All/General",
            TagType.THEME: "Theme",
            TagType.SOURCE: "Source",
            TagType.ARTIST: "Artist",
            TagType.CHARACTER: "Character",
        }

        for tag_type, count in sorted(tag_type_counts):
            type_name = type_names.get(tag_type, f"Unknown ({tag_type})")
            percentage = (count / total_tags) * 100
            print(f"{type_name:15} {count:>8,} tags ({percentage:>5.2f}%)")
        print()

        # 3. Tag frequency distribution
        print("📈 TAG FREQUENCY DISTRIBUTION")
        print("-" * 80)
        print("How many times each tag is used across all images:")
        print()

        # Get tag usage counts
        tag_usage = session.execute(
            select(Tags.tag_id, func.count(TagLinks.image_id).label('usage_count'))
            .outerjoin(TagLinks, Tags.tag_id == TagLinks.tag_id)
            .group_by(Tags.tag_id)
        ).all()

        usage_counts = [usage for _, usage in tag_usage]
        usage_counter = Counter()

        # Bucket by frequency ranges
        for count in usage_counts:
            if count == 0:
                usage_counter['0 (unused)'] += 1
            elif count < 10:
                usage_counter['1-9'] += 1
            elif count < 50:
                usage_counter['10-49'] += 1
            elif count < 100:
                usage_counter['50-99'] += 1
            elif count < 500:
                usage_counter['100-499'] += 1
            elif count < 1000:
                usage_counter['500-999'] += 1
            elif count < 5000:
                usage_counter['1,000-4,999'] += 1
            elif count < 10000:
                usage_counter['5,000-9,999'] += 1
            else:
                usage_counter['10,000+'] += 1

        print(f"{'Usage Range':>20} {'Tag Count':>12} {'Percentage':>12}")
        print("-" * 45)
        for range_name in ['0 (unused)', '1-9', '10-49', '50-99', '100-499',
                           '500-999', '1,000-4,999', '5,000-9,999', '10,000+']:
            count = usage_counter.get(range_name, 0)
            percentage = (count / total_tags) * 100
            print(f"{range_name:>20} {count:>12,} {percentage:>11.2f}%")
        print()

        # 4. Tags suitable for ML training
        print("🎯 ML TRAINING VIABILITY")
        print("-" * 80)

        thresholds = [50, 100, 500, 1000, 5000]
        for threshold in thresholds:
            count = sum(1 for usage in usage_counts if usage >= threshold)
            percentage = (count / total_tags) * 100
            print(f"Tags with ≥{threshold:>5} examples: {count:>6,} tags ({percentage:>5.2f}%)")
        print()

        print("💡 Recommendation:")
        viable_tags = sum(1 for usage in usage_counts if usage >= 100)
        if viable_tags >= 1000:
            print(f"   ✅ You have {viable_tags:,} tags with ≥100 examples - EXCELLENT for training!")
        elif viable_tags >= 500:
            print(f"   ✅ You have {viable_tags:,} tags with ≥100 examples - GOOD for training")
        else:
            print(f"   ⚠️  You have {viable_tags:,} tags with ≥100 examples - consider starting with top tags only")
        print()

        # 5. Top 20 most common tags
        print("🏆 TOP 20 MOST COMMON TAGS")
        print("-" * 80)

        top_tags = session.execute(
            select(
                Tags.tag_id,
                Tags.title,
                Tags.type,
                func.count(TagLinks.image_id).label('usage_count')
            )
            .join(TagLinks, Tags.tag_id == TagLinks.tag_id)
            .group_by(Tags.tag_id, Tags.title, Tags.type)
            .order_by(func.count(TagLinks.image_id).desc())
            .limit(20)
        ).all()

        print(f"{'Rank':>4} {'Tag ID':>8} {'Type':>12} {'Usage':>10} {'Tag Title'}")
        print("-" * 80)
        for rank, (tag_id, title, tag_type, usage) in enumerate(top_tags, 1):
            type_name = type_names.get(tag_type, "Unknown")
            print(f"{rank:>4} {tag_id:>8} {type_name:>12} {usage:>10,} {title}")
        print()

        # 6. Tags per image distribution
        print("📸 TAGS PER IMAGE DISTRIBUTION")
        print("-" * 80)

        tags_per_image = session.execute(
            select(func.count(TagLinks.tag_id).label('tag_count'))
            .group_by(TagLinks.image_id)
        ).all()

        tag_counts = [count for (count,) in tags_per_image]
        tag_count_dist = Counter()

        for count in tag_counts:
            if count == 0:
                tag_count_dist['0'] += 1
            elif count == 1:
                tag_count_dist['1'] += 1
            elif count <= 3:
                tag_count_dist['2-3'] += 1
            elif count <= 5:
                tag_count_dist['4-5'] += 1
            elif count <= 10:
                tag_count_dist['6-10'] += 1
            elif count <= 20:
                tag_count_dist['11-20'] += 1
            elif count <= 50:
                tag_count_dist['21-50'] += 1
            else:
                tag_count_dist['50+'] += 1

        print(f"{'Tags per Image':>15} {'Image Count':>15} {'Percentage':>12}")
        print("-" * 45)
        for range_name in ['0', '1', '2-3', '4-5', '6-10', '11-20', '21-50', '50+']:
            count = tag_count_dist.get(range_name, 0)
            percentage = (count / total_images) * 100
            print(f"{range_name:>15} {count:>15,} {percentage:>11.2f}%")

        if tag_counts:
            avg_tags = sum(tag_counts) / len(tag_counts)
            median_tags = sorted(tag_counts)[len(tag_counts) // 2]
            max_tags = max(tag_counts)
            min_tags = min(tag_counts)

            print()
            print(f"Average:  {avg_tags:.2f} tags per image")
            print(f"Median:   {median_tags} tags per image")
            print(f"Min:      {min_tags} tags per image")
            print(f"Max:      {max_tags} tags per image")
        print()

        # 7. Tag type frequency in usage
        print("📊 TAG TYPE USAGE FREQUENCY")
        print("-" * 80)
        print("How often each tag type appears in tag applications:")
        print()

        type_usage = session.execute(
            select(Tags.type, func.count(TagLinks.image_id).label('usage_count'))
            .join(TagLinks, Tags.tag_id == TagLinks.tag_id)
            .group_by(Tags.type)
        ).all()

        for tag_type, usage in sorted(type_usage):
            type_name = type_names.get(tag_type, f"Unknown ({tag_type})")
            percentage = (usage / total_tag_links) * 100
            print(f"{type_name:15} {usage:>12,} applications ({percentage:>5.2f}%)")
        print()

        # 8. Summary and recommendations
        print("=" * 80)
        print("💡 SUMMARY & RECOMMENDATIONS")
        print("=" * 80)
        print()

        viable_100 = sum(1 for usage in usage_counts if usage >= 100)
        viable_500 = sum(1 for usage in usage_counts if usage >= 500)

        print("Dataset Quality for ML Training:")
        print()
        print(f"✓ Dataset size: {total_images:,} images - {'EXCELLENT' if total_images >= 100000 else 'GOOD'}")
        print(f"✓ Tags per image: {total_tag_links / total_images:.1f} avg - {'EXCELLENT' if (total_tag_links / total_images) >= 10 else 'GOOD'}")
        print(f"✓ Trainable tags (≥100 examples): {viable_100:,} tags")
        print(f"✓ High-quality tags (≥500 examples): {viable_500:,} tags")
        print()

        print("Recommended Training Strategy:")
        print()
        if viable_500 >= 1000:
            print("→ START: Fine-tune WD14 Tagger on your full dataset")
            print("→ NEXT:  Train custom model on top 2,000-5,000 tags")
            print("→ GOAL:  Achieve 70-80% precision on Theme tags within 4-6 weeks")
        elif viable_100 >= 500:
            print("→ START: Fine-tune WD14 Tagger, focus on top 1,000-2,000 tags")
            print("→ NEXT:  Evaluate performance before custom training")
            print("→ GOAL:  Achieve 60-70% precision on Theme tags within 6-8 weeks")
        else:
            print("→ START: Use pre-trained WD14 Tagger with tag mapping")
            print("→ NEXT:  Collect more tagged data before custom training")
            print("→ GOAL:  Establish baseline and gather training requirements")
        print()
        print("=" * 80)


if __name__ == "__main__":
    try:
        analyze_tag_distribution()
    except Exception as e:
        print(f"❌ Error analyzing tags: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
