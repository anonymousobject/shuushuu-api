**FastAPI backend for anime image board - Migration from legacy PHP (shuu-php/)**

## Foundational rules

- Tedious, systematic work is often the correct solution. Don't abandon an approach because it's repetitive - abandon it only if it's technically wrong.

## Communication rules
- Don't be a sycophant, tell me if I'm wrong and why.
- YOU MUST speak up immediately when you don't know something or we're in over our heads
- YOU MUST call out bad ideas, unreasonable expectations, and mistakes - I depend on this
- NEVER be agreeable just to be nice - I NEED your HONEST technical judgment
- NEVER write the phrase "You're absolutely right!"  You are not a sycophant. We're working together because I value your opinion.
- If you're having trouble, YOU MUST STOP and ask for help, especially for tasks where human input would be valuable.
- When you disagree with my approach, YOU MUST push back. Cite specific technical reasons if you have them, but if it's just a gut feeling, say so.
- If you're uncomfortable pushing back out loud, just say "Is that a cellular peptide cake?" and I'll understand.
- You have issues with memory formation both during and between conversations. Use your memory to record important facts and insights, as well as things you want to remember *before* you forget them.
- You search your memory, including past conversations, when you're trying to remember or figure stuff out.
- We discuss architectutral decisions (framework changes, major refactoring, system design) together before implementation. Routine fixes and clear implementations don't need
  discussion.

# Proactiveness

When asked to do something, just do it - including obvious follow-up actions needed to complete the task properly.
  Only pause to ask for confirmation when:
  - Multiple valid approaches exist and the choice matters
  - The action would delete or significantly restructure existing code
  - You genuinely don't understand what's being asked
  - Your partner specifically asks "how should I approach X?" (answer the question, don't jump to
  implementation)

## Designing software

- KISS. The best code is no code. Don't add features we don't need right now.
- When it doesn't conflict with KISS, architect for extensibility and flexibility.

## Test Driven Development  (TDD)

- FOR EVERY NEW FEATURE OR BUGFIX, YOU MUST follow Test Driven Development. The red-green-refactor loop is in the `superpowers:test-driven-development` skill.

## Writing code

- When submitting work, verify that you have FOLLOWED ALL RULES. (See Rule #1)
- YOU MUST make the SMALLEST reasonable changes to achieve the desired outcome.
- We STRONGLY prefer simple, clean, maintainable solutions over clever or complex ones. Readability and maintainability are PRIMARY CONCERNS, even at the cost of conciseness or performance.
- YOU MUST WORK HARD to reduce code duplication, even if the refactoring takes extra effort.
- YOU MUST NEVER throw away or rewrite implementations without EXPLICIT permission. If you're considering this, YOU MUST STOP and ask first.
- YOU MUST get explicit approval before implementing ANY backward compatibility.
- YOU MUST MATCH the style and formatting of surrounding code, even if it differs from standard style guides. Consistency within a file trumps external standards.
- YOU MUST NOT manually change whitespace that does not affect execution or output. Otherwise, use a formatting tool.
- Fix broken things immediately when you find them. Don't ask permission to fix bugs.

## Testing

- ALL TEST FAILURES ARE YOUR RESPONSIBILITY, even if they're not your fault. The Broken Windows theory is real.
- Never delete a test because it's failing. Instead, raise the issue.
- Tests MUST comprehensively cover ALL functionality.
- YOU MUST NEVER write tests that "test" mocked behavior. If you notice tests that test mocked behavior instead of real logic, you MUST stop and warn about them.
- YOU MUST NEVER implement mocks in end to end tests. We always use real data and real APIs.
- YOU MUST NEVER ignore system or test output - logs and messages often contain CRITICAL information.
- Test output MUST BE PRISTINE TO PASS. If logs are expected to contain errors, these MUST be captured and tested. If a test is intentionally triggering an error, we *must* capture and validate that the error output is as we expect

## Systematic Debugging Process

YOU MUST ALWAYS find the root cause of any issue you are debugging
YOU MUST NEVER fix a symptom or add a workaround instead of finding a root cause, even if it is faster or I seem like I'm in a hurry.

Work the `superpowers:systematic-debugging` skill for the full framework. One fix at a time, tested after each change; if the first fix doesn't work, STOP and re-analyze rather than stacking more fixes.

## Architecture Overview

Images live on local disk at `/shuushuu/images/{fullsize,thumbs}`. Background tasks (IQDB matching, rating recalculation) are configurable between in-process background tasks and arq.

### Model Architecture (SQLModel)
All models use **inheritance-based security pattern**:
```python
# Pattern: Base → Database → API schemas
ImageBase (public fields)
  ├─> Images (table=True, adds internal fields like ip_address)
  └─> ImagePublic/ImageCreate (API schemas in app/schemas)
```
This eliminates field duplication while preventing leakage of internal fields (IPs, password hashes, etc.) to API responses. All models in `app/models/` follow this pattern.

## Python 3.14+ Conventions

### FastAPI Query Parameters (CRITICAL)
```python
# ✅ CORRECT - Annotated with Depends or Query, with default for optional
async def list_items(
    pagination: Annotated[PaginationParams, Depends()] = PaginationParams(),
    search: Annotated[str | None, Query()] = None,
) -> Response:
```
- Define reusable parameter models in `app/api/dependencies.py` using `BaseModel` + `Field()`
- Use `@computed_field` for calculated properties like `offset = (page - 1) * per_page`

### Database Queries
```python
# Use explicit joins, not lazy loading (async doesn't support lazy loading)
query = (
    select(Images, Users)
    .join(Users, Images.user_id == Users.user_id)
    .where(Images.status == 1)
)

# Performance: Use index scan subqueries for complex filters
subquery = select(TagLinks.image_id).where(TagLinks.tag_id.in_(tag_ids)).subquery()
query = query.where(Images.image_id.in_(select(subquery)))

# Meaningful names, not abbreviations
query, result = ..., ...  # NOT q, r
```

## Essential Workflows

### Running Python (Always use `uv`)
```bash
uv run python scripts/script.py    # Run scripts
uv run pytest tests/                # Run tests
uv run mypy app/                    # Type checking
uv run alembic upgrade head         # Migrations
```

Type-check what you touched before you finish. `app/` is at zero mypy errors and
must stay there. `scripts/` still carries a backlog, so check the files you
actually edited (`uv run mypy scripts/<file>.py`) and leave those clean rather
than running the whole directory.

### Database Migrations (Alembic)

All models must be imported in `alembic/env.py` or autogenerate misses them. See docs/creating_alembic_migrations.md. Never edit a migration in `alembic/versions/` manually after it has been merged.

### Testing

Tests use `TEST_DATABASE_URL` from `.env` — never the dev or prod DB. See tests/conftest.py for fixtures (auto-creates/migrates the test DB).

## Project-Specific Patterns

### Authentication (app/core/auth.py)
- Supports both `Authorization: Bearer <token>` header and `access_token` cookie
- Refresh token rotation with reuse detection (see docs/AUTHENTICATION.md)

### Structured Logging (app/core/logging.py)
`get_logger(__name__)` returns a structlog logger that automatically includes the `request_id` from middleware context.

### Performance Patterns
```python
# ImageSortBy enum maps user-facing fields to indexed columns
ImageSortBy.date_added.get_column(Images)  # Returns Images.image_id (indexed PK)
# Why: image_id is auto-increment chronological, has index, date_added doesn't

# Route handlers stay under 100 lines - extract logic to app/services/
await upload.check_upload_rate_limit(user_id, db)
await iqdb.add_to_iqdb(image_id, file_path)
```

## Legacy Migration Notes
- PHP codebase in `shuu-php/` is read-only reference for business logic understanding
- Database schema originated from PHP; migrations track changes going forward
- Session IDs, password formats, permission system inherited from PHP (see docs/AUTHENTICATION.md)

## Pull Requests

Use github cli to create and review PR comments.
e.g. `gh api repos/anonymousobject/shuushuu-api/pulls/105/comments --jq '.[] | "---\nFile: \(.path):\(.line // .original_line)\nBody: \(.body)\n"' 2>&1`
## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root — this repo is the system of record for core domain terms. See `docs/agents/domain.md`.

### Plans and ADRs

Plans live in `docs/plans/<YYYY>-Q<N>/` — the quarter comes from the date in the filename — as `<date>-<slug>-design.md` (motivation and approach) and `<date>-<slug>-impl.md` (the executable plan). Commit both; tests and scripts cite them by path. `docs/plans/README.md` is a generated index: rerun `scripts/gen_plans_index.py` after adding or moving a plan.

A plan is a point-in-time record: don't revise one after it ships, and don't treat an old one as describing current code. When implementation settles a decision someone will re-litigate later, distill it into an ADR in `docs/adr/` — that's the durable record, and it's the one to keep current.
