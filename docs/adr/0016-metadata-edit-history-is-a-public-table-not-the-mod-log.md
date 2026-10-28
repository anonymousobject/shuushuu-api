# Metadata edit history is a public table, not the mod log

Edits to an image's `source_url` and `miscmeta` (the frontend's "Source" and
"Notes") are recorded in `image_metadata_history` — one row per field per
`PATCH /images/{id}`, with the old and new value and the editing user — since
September 2026 (api#407, plan
`docs/plans/2026-Q3/2026-09-24-image-info-editing-impl.md`). The rows are
public: `GET /images/{id}/metadata-history` needs no auth, and they appear in
`GET /users/{id}/history` as kind 5. `admin_actions` is not written.

## Considered Options

- **Logging the edit in `admin_actions`** was rejected. `admin_actions` is the
  private moderation log: its details carry mod comments and review-vote
  notes, so it cannot be exposed row-by-row, and a public view of it would
  have to filter by action type and redact fields. Metadata edits are made
  by owners and taggers as well as staff and were wanted as ordinary public
  history, alongside tag changes.
- **Recording only that an edit happened, without values,** was rejected.
  The point of the history is to see what a source used to be and who
  changed it; that is the same information the tag history already shows.
- **A new public table, following `image_status_history`,** won. Status
  changes already established that public audit rows live apart from the
  mod log; this keeps the two audiences separate at the table level rather
  than by column.

## Consequences

- Public means the values are public. Nothing private may be stored in
  `source_url` or `miscmeta`; they are validated (http(s) only, length
  limits, trimmed) but not redacted.
- Old and new values are captured under the image row's `FOR NO KEY UPDATE`
  lock inside the same transaction as the update, so concurrent edits
  produce a consistent chain rather than two rows claiming the same old
  value.
- Adding a field to the editable set means adding it to
  `ImageMetadataField`, the schema's `field` literal, and the history
  builder; the frontend's label map follows the generated type.
- A user's deletion sets `user_id` to NULL rather than removing the rows;
  the edit stays in the image's history with no author.
