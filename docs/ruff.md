# Ruff - Python Linting & Formatting

This project uses [Ruff](https://docs.astral.sh/ruff/) for both linting and formatting, replacing Black and other tools.

## VSCode Integration

The Ruff extension is configured in `.vscode/settings.json` to:
- ✅ Format on save
- ✅ Auto-fix linting issues on save
- ✅ Organize imports automatically
- ✅ Use project's `pyproject.toml` configuration

## Command Line Usage

### Formatting

```bash
# Check if files need formatting (doesn't modify)
uv run ruff format --check .

# Format all files
uv run ruff format .

# Format specific files
uv run ruff format app/models/

# Show what would change without modifying
uv run ruff format --diff app/
```

### Linting

```bash
# Check for linting issues
uv run ruff check .

# Auto-fix issues
uv run ruff check --fix .

# Show statistics
uv run ruff check --statistics .

# Check specific rules
uv run ruff check --select E,F .
```

### Combined Workflow

```bash
# Format and lint in one go
uv run ruff format . && uv run ruff check --fix .
```

## Configuration

All Ruff configuration is in `pyproject.toml`:

- **Line length**: 100 characters
- **Target Python**: 3.12
- **Formatter**: Double quotes, 4-space indent
- **Linter rules**: pycodestyle, pyflakes, isort, bugbear, comprehensions, pyupgrade
- **Import sorting**: Automatic with first-party package detection

## Pre-commit Hook (Optional)

To run Ruff automatically before commits, add to `.git/hooks/pre-commit`:

```bash
#!/bin/sh
uv run ruff format --check . || exit 1
uv run ruff check . || exit 1
```

## Migration from Black

Ruff's formatter is designed to be a drop-in replacement for Black:
- Compatible formatting output
- ~10-100x faster
- Single tool for both linting and formatting

The Black configuration has been commented out in `pyproject.toml`.

## Ignoring Rules

To ignore specific rules in a file:

```python
# ruff: noqa: E501  - Ignore line-too-long for this file

def foo():
    x = 1  # noqa: F841  - Ignore unused variable for this line
```

## Common Rules

- **E**: pycodestyle errors (PEP 8 violations)
- **F**: Pyflakes (undefined names, unused imports)
- **I**: isort (import sorting)
- **B**: flake8-bugbear (common bugs)
- **UP**: pyupgrade (modern Python syntax)
- **FA**: future annotations (type hint modernization)

## Resources

- [Ruff Documentation](https://docs.astral.sh/ruff/)
- [Rule Reference](https://docs.astral.sh/ruff/rules/)
- [VSCode Extension](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff)
