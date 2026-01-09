# Comment Search: LIKE vs FULLTEXT Comparison

This document explains the two comment search endpoints and how to benchmark their performance.

## Overview

The API provides two search endpoints for comments:

1. **`/comments/search/text`** - Pattern matching with SQL `LIKE`
2. **`/comments/search/fulltext`** - MySQL native full-text search

## Search Endpoints

### 1. LIKE Pattern Matching (`/comments/search/text`)

**Endpoint:** `GET /api/v1/comments/search/text`

**How it works:**
- Uses SQL `LIKE '%query%'` pattern matching
- Scans through all comment text
- Case-insensitive partial matching
- No special setup required

**Pros:**
- Works out of the box, no index needed
- Simple substring matching
- Good for small datasets

**Cons:**
- Cannot use indexes effectively (full table scan)
- Slow on large datasets (10k+ rows)
- No relevance ranking
- No advanced search features

**Example:**
```bash
curl "http://localhost:8000/api/v1/comments/search/text?query_text=awesome"
```

### 2. Full-Text Search (`/comments/search/fulltext`)

**Endpoint:** `GET /api/v1/comments/search/fulltext`

**How it works:**
- Uses MySQL's native `MATCH() AGAINST()` syntax
- Leverages FULLTEXT index
- Supports natural language and boolean search modes
- Word stemming and stop word filtering

**Pros:**
- 10-100x faster on large datasets
- Natural language relevance ranking
- Boolean operators: `+required -excluded "exact phrase" word*`
- Designed for text search workloads

**Cons:**
- Requires FULLTEXT index (see setup below)
- Minimum word length (default: 3-4 characters)
- Ignores common stop words (the, is, are, etc.)
- Slightly slower on very small datasets

**Search Modes:**

- **Natural Language** (default): Ranks results by relevance
  ```bash
  curl "http://localhost:8000/api/v1/comments/search/fulltext?query_text=awesome&mode=natural"
  ```

- **Boolean**: Advanced operators for precise queries
  ```bash
  # Must contain "awesome", must not contain "terrible"
  curl "http://localhost:8000/api/v1/comments/search/fulltext?query_text=%2Bawesome%20-terrible&mode=boolean"

  # Exact phrase
  curl "http://localhost:8000/api/v1/comments/search/fulltext?query_text=%22really%20awesome%22&mode=boolean"

  # Wildcard
  curl "http://localhost:8000/api/v1/comments/search/fulltext?query_text=awe*&mode=boolean"
  ```

## Setup: Creating the FULLTEXT Index

The full-text search endpoint requires a FULLTEXT index on the `post_text` column.

### Option 1: Run the Migration SQL

```bash
mysql -u your_user -p your_database < docs/fulltext_index_migration.sql
```

### Option 2: Manual SQL

```sql
CREATE FULLTEXT INDEX idx_post_text_fulltext ON posts(post_text);
```

### Option 3: Create an Alembic Migration

```bash
# Generate migration
alembic revision -m "Add fulltext index to posts.post_text"

# Edit the generated file in alembic/versions/ and add:
# def upgrade():
#     op.execute("CREATE FULLTEXT INDEX idx_post_text_fulltext ON posts(post_text)")
#
# def downgrade():
#     op.execute("DROP INDEX idx_post_text_fulltext ON posts")

# Run migration
alembic upgrade head
```

### Verify the Index

```sql
SHOW INDEX FROM posts WHERE Key_name = 'idx_post_text_fulltext';
```

## Performance Benchmarking

Use the included benchmark script to compare performance:

```bash
# Basic usage
python docs/test_search_performance.py

# Custom queries
python docs/test_search_performance.py --queries "awesome" "beautiful" "great picture"

# More runs for statistical accuracy
python docs/test_search_performance.py --runs 20

# Different API URL
python docs/test_search_performance.py --base-url http://api.example.com
```

### Sample Output

```
======================================================================
PERFORMANCE COMPARISON SUMMARY
======================================================================

Query: 'awesome'
Results found: LIKE=1247, FULLTEXT=1247

Metric               LIKE (ms)       FULLTEXT (ms)   Speedup
----------------------------------------------------------------------
Mean                 234.56          12.34           19.01x
Median               231.23          11.89           19.45x
Min                  218.45          10.23           21.35x
Max                  267.89          15.67           17.09x
Std Dev              15.23           1.45            N/A

Overall Verdict:     FULLTEXT is 19.01x faster! 🚀
```

## When to Use Which Endpoint

### Use LIKE Search (`/search/text`) when:
- You have a small dataset (< 10k comments)
- You need exact substring matching
- You haven't created the FULLTEXT index yet
- You're searching for very short strings (1-2 chars)

### Use FULLTEXT Search (`/search/fulltext`) when:
- You have a large dataset (> 10k comments)
- You want relevance-based ranking
- You need advanced search features (boolean operators, phrases)
- Performance is critical
- You've created the FULLTEXT index

## Advanced Configuration

### Minimum Word Length

MySQL ignores words shorter than `innodb_ft_min_token_size` (default: 3).

To change:
```sql
-- Show current setting
SHOW VARIABLES LIKE 'innodb_ft_min_token_size';

-- Change (requires server restart and index rebuild)
SET GLOBAL innodb_ft_min_token_size = 2;
```

Then rebuild the index:
```sql
DROP INDEX idx_post_text_fulltext ON posts;
CREATE FULLTEXT INDEX idx_post_text_fulltext ON posts(post_text);
```

### Stop Words

Common words like "the", "is", "at" are ignored by default.

To disable stop words:
```sql
SET GLOBAL innodb_ft_enable_stopword = 0;
-- Then rebuild the index
```

## Performance Tips

1. **Always create the index** - FULLTEXT search without the index will fail
2. **Use natural mode for general searches** - Better relevance ranking
3. **Use boolean mode for precise queries** - When you know exactly what you want
4. **Monitor index size** - FULLTEXT indexes are ~50-70% of column data size
5. **Consider your dataset** - LIKE can be faster for very small tables (< 1000 rows)

## Troubleshooting

**Error: "Can't find FULLTEXT index matching the column list"**
- Solution: Create the FULLTEXT index (see Setup section)

**No results in FULLTEXT but LIKE works**
- Cause: Query words are too short or are stop words
- Solution: Use longer words or disable stop words

**FULLTEXT slower than LIKE**
- Cause: Dataset is very small (< 1000 rows)
- Solution: Use LIKE for small datasets, FULLTEXT for large ones

**Boolean search not working**
- Cause: Using natural mode instead of boolean mode
- Solution: Add `&mode=boolean` to the URL
