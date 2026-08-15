---
name: plan-close-out
description: Use after merging a PR that implemented a plan in docs/plans/ - records whether the plan shipped, distills any decision worth keeping into an ADR, and regenerates the plans index.
---

# Closing out a plan

Run this once, after the PR merges.

## 1. Identify the plan

The PR body's `Plan:` line names it. Failing that, match the branch or feature to
a file under `docs/plans/<quarter>/`.

## 2. Record the outcome

Edit `docs/plans/README.md` **between the `<!-- exceptions:start -->` and
`<!-- exceptions:end -->` markers only** — everything outside them is generated
and will be overwritten.

- **Shipped** — remove its row if one is listed. Implemented is the default, so
  a shipped plan needs no row at all.
- **Deferred or abandoned** — add a row:
  `| [name](<quarter>/<file>.md) | deferred | one line on why |`

Leave the plan file itself untouched. A plan records what was intended on its
date; it is not a description of what shipped.

## 3. Distill a decision, if there was one

Did the work settle something a future reader would otherwise re-litigate — a
tradeoff with a real alternative, a rejected approach, a constraint the code
can't explain on its own? If so, write `docs/adr/NNNN-<slug>.md` in the shape of
the existing ones: the decision as the title, the options considered and why they
lost, and the consequences that follow.

Not every PR earns an ADR. Most don't. The test is whether someone would
reasonably propose the opposite later.

## 4. Regenerate the index

```bash
uv run python scripts/gen_plans_index.py
```

Commit the regenerated `docs/plans/README.md` with any ADR you added.
