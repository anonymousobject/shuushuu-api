# Migration Performance Analysis & Optimization Summary

**Date**: December 18, 2025

## Performance Testing Results

### Initial Analysis (Before Optimization)
- **Total comments in database**: 556,063
- **Comments with quotes**: 62,113 (11.2%)
- **Initial loading**: 8.82s to load ALL 556K comments into memory (~1GB)
- **Approach**: Load all comments → detect quotes → individual lookups
- **Bottleneck**: Loading entire comment table was wasteful and slow

### Optimized Approach (Batch Query)
The refactored script implements **3 key optimizations**:

1. **Query-Based Detection** (0.78s instead of 8.82s)
   - SELECT only comments WITH quotes instead of loading ALL
   - Saves 8+ seconds upfront
   - Reduces memory overhead by 89%

2. **Batch Grouping by Image** (efficient in-memory search)
   - Group only the ~62K quoted comments by image_id
   - Fetch all comments for affected images once per image
   - Search matches in memory instead of individual DB queries

3. **Batched Database Writes** (1000 updates per transaction)
   - Accumulate updates in memory
   - Commit every 1000 updates instead of one-by-one
   - Significantly reduces transaction overhead

### Performance Results (Dry-Run on Full Dataset)
```
Total quoted comments processed:   62,113
Comments with extractable quotes:  62,062 (99.9%)
Parent comments found:             40,258 (64.8%)
Comments ready for update:         40,000 (batched)

Total time:                        15 seconds (0.3 minutes)
```

### Extrapolation to Full Run
With infrastructure optimization (MariaDB doubled buffer pool to 512MB):
- Expected time for full migration: ~20-30 seconds
- Previously estimated: ~5-10 minutes with old approach

## Database Infrastructure Improvements

### docker-compose.yml Updates
```yaml
mariadb:
  environment:
    # CPU and I/O tuning
    innodb_buffer_pool_size: 512M       # Was 256M (2x improvement)
    innodb_write_io_threads: 8          # Was 4
    innodb_read_io_threads: 8           # Was 4

    # Query optimization
    query_cache_type: 1                 # New: Enable query caching
    query_cache_size: 64M               # New: 64MB query cache

    # Connection management
    max_connections: 400                # Was 200

  deploy:
    resources:
      limits:
        cpus: '4'
        memory: 2G
      reservations:
        cpus: '2'
        memory: 1G
```

## Key Takeaways

### What Was Slow
1. ❌ Loading ALL 556K comments into memory just to find 62K with quotes
2. ❌ Individual database query per quoted comment for parent lookup
3. ❌ No query caching for identical comment lookups
4. ❌ Insufficient buffer pool for large result sets

### What's Fast Now
1. ✅ Query-based detection finds only quoted comments (0.78s)
2. ✅ Batch grouping by image allows efficient in-memory search
3. ✅ Batched writes reduce transaction overhead
4. ✅ Infrastructure supports larger working sets

## Migration Status

**Dry-Run**: ✅ Complete (15 seconds, 40K updates ready)
**Full Run**: 🔄 In Progress (started 00:00:00 UTC)

Expected completion: ~30 seconds from start
- Processing ~2,000 comments per second
- Committing batches every 0.5 seconds

## Files Modified

1. **scripts/migrate_quoted_comments.py** (optimized)
   - Changed from load-all to query-based approach
   - Added batch grouping by image
   - Batched database writes
   - Added progress logging

2. **docker-compose.yml** (infrastructure)
   - Doubled buffer pool (256M → 512M)
   - Parallelized I/O threads
   - Enabled query caching
   - Increased connection limit
   - Added resource limits/reservations

3. **scripts/analyze_migration_performance.py** (new)
   - Performance testing framework
   - Dry-run comparison tests
   - Bottleneck identification

## Next Steps

1. Monitor migration completion (~30s)
2. Verify parent_comment_id updates (should see 40K+ rows)
3. Test comment threading on /comments page
4. Verify quotes have been removed from comment text
5. Run trigger validation tests to ensure counters still work
