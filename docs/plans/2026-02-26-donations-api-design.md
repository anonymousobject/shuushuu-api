# Donations API Design

## Goal

Expose donation data through the API so the frontend can display recent donations and monthly totals. Allow admins to create donation records.

## Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/donations` | Public | Last X donations |
| `GET` | `/api/v1/donations/monthly` | Public | Monthly totals for last Y months |
| `POST` | `/api/v1/donations` | `DONATIONS_CREATE` | Create a donation record |

## `GET /donations` — Recent Donations

**Query params:**
- `limit: int` — number of recent donations to return (default 10, max 50)

**Response:** `DonationListResponse`
```json
{
  "donations": [
    { "date": "2019-03-05T08:27:57", "amount": 10, "nick": null, "user_id": 754271 },
    { "date": "2018-02-22T13:46:12", "amount": 5, "nick": "Stefan K.", "user_id": 0 }
  ]
}
```

Ordered by date descending.

## `GET /donations/monthly` — Monthly Totals

**Query params:**
- `months: int` — how many months back to include (default 6, max 24)

**Response:** `MonthlyDonationResponse`
```json
{
  "monthly_totals": [
    { "year": 2019, "month": 3, "total": 10 },
    { "year": 2018, "month": 2, "total": 5 }
  ]
}
```

Ordered by most recent month first. Uses `GROUP BY YEAR(date), MONTH(date)` with `SUM(amount)`.

## `POST /donations` — Create Donation

**Auth:** Requires `DONATIONS_CREATE` permission.

**Request body:** `DonationCreate`
```json
{ "amount": 10, "nick": "Anonymous", "user_id": null, "date": null }
```

- `amount: int` — required
- `nick: str | None` — optional, max 30 chars
- `user_id: int | None` — optional
- `date: datetime | None` — optional, defaults to current timestamp

**Response:** `DonationResponse` (same shape as list items)

## Schemas

**DonationResponse:**
- `date: datetime`
- `amount: int`
- `nick: str | None`
- `user_id: int | None`

**DonationListResponse:**
- `donations: list[DonationResponse]`

**MonthlyDonationTotal:**
- `year: int`
- `month: int`
- `total: int`

**MonthlyDonationResponse:**
- `monthly_totals: list[MonthlyDonationTotal]`

**DonationCreate:**
- `amount: int`
- `nick: str | None = None` (max 30)
- `user_id: int | None = None`
- `date: datetime | None = None`

## Implementation Notes

- No Users join needed — `nick` is the display field, `user_id` is unreliable (many are 0 or reference deleted users)
- Existing `Donations` model in `app/models/misc.py` is sufficient
- New `DONATIONS_CREATE` permission in `app/core/permissions.py`
- New files: `app/api/v1/donations.py`, `app/schemas/donations.py`
- Register router in v1 API router

## Data Notes

- 164 donations in the database (as of 2026-02-26)
- 37 rows have `user_id=0` (legacy anonymous), 4 have non-zero `user_id` with no matching user
- `nick` field carries the donor display name in virtually all cases
