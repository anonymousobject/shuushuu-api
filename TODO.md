# TODO Items

## Content Migration

### Convert Legacy BBCode to Markdown
**Priority:** Medium
**Status:** Not Started

Legacy image comments in the database use BBCode formatting (`[quote]`, `[spoiler]`, `[url]`).
These need to be converted to Markdown format for consistency with the new API.

**Tasks:**
1. Create migration script to convert BBCode → Markdown:
   - `[quote="author"]text[/quote]` → `> **author wrote:** text`
   - `[spoiler]text[/spoiler]` → `> **Spoiler:** text`
   - `[url]link[/url]` → `link` (auto-linked)
   - `[url=link]text[/url]` → `[text](link)`
2. Test conversion on sample data
3. Run migration against production data (requires downtime or versioned API)
4. Remove BBCode parser from PHP codebase once migration complete

**Files Affected:**
- `app/models/comment.py` - `post_text` field contains BBCode
- Legacy PHP: `shuu-php/common/functions/image.php` - `applybbCode()` function
- New parser: `app/utils/markdown.py`

**Notes:**
- Consider keeping BBCode parser temporarily for backwards compatibility
- May need dual-mode rendering during transition period
- Check if any users have BBCode in their private messages (shouldn't exist but verify)
