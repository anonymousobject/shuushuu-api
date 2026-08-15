# Tag Proposal System Design

## Problem

Only users with `TAG_CREATE` permission can create tags. Regular users who discover missing tags have no way to request them. This bottlenecks tag creation on a small group of privileged users.

## Solution

A proposal system that lets any registered user suggest new tags, with a tiered review process that scales with the subjectivity of the tag type.

## Lifecycle

### Artist / Source / Character tags (objective, verifiable)

```
Proposed --> Staff Review (margin 2, 5-day deadline) --> Approved / Rejected
```

### Theme tags (subjective, debatable)

```
Proposed --> Staff Review (margin 3, 7-day deadline) --> Community Vote (66% approval, 14-day deadline) --> Approved / Rejected
```

## Data Model

### tag_proposals

| Column | Type | Notes |
|--------|------|-------|
| proposal_id | int PK | |
| title | str(150) | Proposed tag name |
| type | int | Tag type (1=Theme, 2=Source, 3=Artist, 4=Character) |
| description | str(500) | Justification for the tag |
| user_id | FK users | Proposer |
| status | int | PENDING_REVIEW=0, STAFF_REVIEW=1, COMMUNITY_VOTE=2, APPROVED=3, REJECTED=4 |
| tag_id | FK tags, nullable | Set when tag is auto-created on approval |
| review_deadline | datetime, nullable | Staff review deadline |
| vote_deadline | datetime, nullable | Community vote deadline |
| vote_opened_at | datetime, nullable | When community voting started |
| closed_by | FK users, nullable | Who manually resolved/force-closed. NULL = system |
| created_at | datetime | |
| closed_at | datetime, nullable | |

### tag_proposal_votes

| Column | Type | Notes |
|--------|------|-------|
| vote_id | int PK | |
| proposal_id | FK tag_proposals | |
| user_id | FK users | |
| vote | int | 1=approve, 0=reject |
| comment | str(500), nullable | |
| phase | int | STAFF_REVIEW=1, COMMUNITY_VOTE=2 |
| created_at | datetime | |

Unique constraint: `(proposal_id, user_id, phase)`

### tag_proposal_examples

| Column | Type | Notes |
|--------|------|-------|
| example_id | int PK | |
| proposal_id | FK tag_proposals | |
| image_id | FK images | |

## Permissions

- **No permission needed**: Create proposals, vote in community phase (authenticated only)
- `TAG_PROPOSAL_REVIEW`: Vote during staff review phase (taggers/mods/admins)
- `TAG_PROPOSAL_CLOSE`: Force-close a proposal at any phase (mods/admins)
- `TAG_CREATE`: Unchanged, still allows direct tag creation bypassing proposals

## API Endpoints

```
POST   /api/v1/tag-proposals                       -- Create proposal (authenticated)
GET    /api/v1/tag-proposals                       -- List proposals (filter by status, type)
GET    /api/v1/tag-proposals/{proposal_id}         -- Get proposal + votes
PATCH  /api/v1/tag-proposals/{proposal_id}         -- Edit (proposer only, before first vote)
POST   /api/v1/tag-proposals/{proposal_id}/vote    -- Cast vote (phase-aware permission check)
POST   /api/v1/tag-proposals/{proposal_id}/close   -- Force-close (TAG_PROPOSAL_CLOSE)
```

## Phase Transitions

### On vote cast (both phases)

- Staff review: check if approve/reject margin reached threshold
- Community vote: check if >= 80% in one direction AND >= 2 days since vote opened AND minimum votes met

### On deadline expiry (background job)

- Staff review: resolve by majority if minimum votes exist. Otherwise awaits manual TAG_PROPOSAL_CLOSE decision.
- Community vote: if minimum votes met and >= 66% approve, approve. If minimum votes not met, falls back to staff decision.

### On approval

- Auto-create tag from proposal data (title, type, description)
- Set tag_proposals.tag_id, status=APPROVED, closed_at=now

### On rejection

- Set status=REJECTED, closed_at=now

## Proposal Rules

- Proposer can edit until first vote is cast
- Max 3 open proposals per user
- Duplicate detection: reject if tag with same title+type already exists or is already proposed

## Configuration Constants

```
PROPOSAL_STAFF_MARGIN_THEME = 3
PROPOSAL_STAFF_MARGIN_OTHER = 2
PROPOSAL_STAFF_DEADLINE_THEME = 7   # days
PROPOSAL_STAFF_DEADLINE_OTHER = 5   # days
PROPOSAL_VOTE_DEADLINE = 14         # days
PROPOSAL_VOTE_APPROVE_THRESHOLD = 0.66
PROPOSAL_VOTE_EARLY_CLOSE_THRESHOLD = 0.80
PROPOSAL_VOTE_EARLY_CLOSE_MIN_DAYS = 2
PROPOSAL_VOTE_MIN_VOTES = 5
PROPOSAL_MAX_OPEN_PER_USER = 3
```
