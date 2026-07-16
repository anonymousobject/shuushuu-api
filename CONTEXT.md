# Shuushuu

An anime image board: users upload, tag, rate, favorite, and discuss images; moderators curate via reports, reviews, statuses, and an ML-assisted tagging queue. This API defines the core domain; the SvelteKit frontend (shuushuu-frontend) consumes it.

## Language

### ML tag suggestions

**Suggestion-eligible**:
An image status in which ML tag suggestion rows may exist and appear in review surfaces. Exactly `ACTIVE` and `SPOILER`; every other status (REPOST, DEACTIVATED, REVIEW, legacy hidden statuses) is ineligible.
_Avoid_: taggable (any image can be tagged regardless of status), visible/hidden (REPOST pages are publicly visible yet ineligible)

**Implicit approval**:
A pending suggestion resolved to approved because a person applied its tag outside the ML review flow (manual add, batch tagging, report resolution). The tagger is recorded as reviewer.
_Avoid_: auto-approval (implies no human involvement)

**System resolution**:
A pending suggestion resolved to approved by data movement rather than any person's tagging action — the stale-row backfill, or tag migration during repost-marking. Reviewer is empty and displays as "—".
_Avoid_: auto-approval, implicit approval (both imply a human actor)
