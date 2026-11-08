# Contributing

Thanks for taking a look. Two things matter most here, because both APIs are
undocumented and change without notice:

1. **Verify against the real API before changing a request shape.** The mock
   in `tests/conftest.py` reproduces what was observed; it cannot tell you
   that Napper changed. `tools/roundtrip_categories.py` can - it writes one
   entry per category far in the past and cleans up after itself.
2. **Never commit anything from `~/.config/napper-sync/`**, and strip IDs,
   names and tokens from any API output you paste into an issue. Child IDs
   and account IDs are UUIDs; the `.gitignore` catches the obvious files but
   not a pasted JSON blob.

## Setup

```bash
pip install -e ".[dev]"
pytest
ruff check . && ruff format .
```

## Adding a Famly event type

1. Capture one real event with `napper-sync famly calendar <from> <to> --json`
   and anonymise it (replace the child's name in `title`, drop `originator`
   and `invitees`).
2. Add a branch in `famly_mapping.translate()` and a test in
   `tests/test_famly_mapping.py` using the anonymised event.
3. If Napper needs a category or field that `categories.py` does not have,
   confirm the field name against your own history
   (`tools/analyze_categories.py`) first.

## Style

Ruff handles formatting and linting (`ruff format`, `ruff check`). Keep the
standard-library-only rule for the package itself; dev tooling may use
anything.
