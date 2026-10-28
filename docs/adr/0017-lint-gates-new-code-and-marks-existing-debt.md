# Lint gates new code and marks existing debt

Since October 2026 ruff checks maintainability, not only bugs: on top of
`E,W,F,I,B,C4,UP` it runs `SIM`, `RET`, `PERF`, `ARG`, `FAST`, `TID251`
(banning `logging.getLogger` in favour of `get_logger`), `RUF100`, and the
size/complexity rules `C90`, `PLR0911`, `PLR0912`, `PLR0915` at their default
limits (complexity 10, 6 returns, 12 branches, 50 statements). Everything the
new rules found was fixed, except functions already over a size/complexity
limit: those 62 carry a `# noqa` naming the limit they exceed.

## Considered Options

- **Refactoring the 62 oversized functions** in the same change was rejected.
  `list_images` alone measured complexity 55 and 172 statements; splitting
  these is design work that needs its own review, not a lint change.
- **Raising the limits to today's worst case** was rejected. A complexity cap
  of 55 constrains nothing.
- **Gating only changed lines** (reviewdog or similar) was rejected for now. It
  adds tooling, and the `noqa` baseline gives the same effect for the size
  rules while every other rule's backlog was small enough to fix outright.
- **`ERA001` (commented-out code)** was left off. Of 24 hits, about 10 were
  code; the rest were explanatory comments that parse as code, such as a
  formula or a tuple's shape, and each would need its own `noqa`.
- **`PLR0913`/`PLR0917` (argument counts)** were left off. Every FastAPI
  dependency is a parameter, so most of the 70 hits were ordinary route
  handlers. Parameter models (`Annotated[Model, Depends()]`, per AGENTS.md)
  are the lever for handlers with many query params, not a count.
- **`RET504` and `SIM108`** are ignored: a named result before `return`, or an
  if/else block, often reads better than the inline form those rules push for.
- **`ARG` is not applied everywhere.** Tests are exempt (fixtures requested
  only for the rows they set up), `app/tasks/**` is exempt from `ARG001`
  (arq's `WorkerCoroutine` protocol pins the `ctx` name, and a kwarg that
  already-queued jobs still send can't be dropped), and `r2_storage.py` is
  exempt from `ARG002` (`DummyR2Storage` mirrors `R2Storage`'s keyword
  signatures and raises before reading them).

## Consequences

- New functions must fit the limits. `grep -rn 'noqa: C901\|noqa: PLR09'`
  lists the debt.
- `RUF100` ratchets the baseline down: once a marked function fits a limit,
  its `noqa` code is reported as unused and has to go.
- The ratchet does not go the other way. A function that already carries a
  `noqa` can keep growing without a warning; reviewers have to catch that.
- Route dependencies are written as `Annotated` aliases (`DbSession`,
  `RedisClient`, `CurrentUser`, ...); `FAST002` rejects `x: T = Depends(dep)`
  in a route signature.
- Application code logs through `app.core.logging.get_logger`. Only the
  logging setup module and its test may call `logging.getLogger`.
