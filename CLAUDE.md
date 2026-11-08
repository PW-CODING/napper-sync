# CLAUDE.md

Project notes for AI coding assistants working in this repository.

## What this is

`napper-sync` is an unofficial client for the private Napper baby-tracker API
plus a sync that imports a nursery's Famly entries into Napper. Both APIs are
undocumented; everything known about them is in `docs/napper-api.md` and
`docs/famly-api.md`. Read those before touching request shapes.

## Layout

- `src/napper_sync/` - the package. Standard library only; keep it that way.
  `napper.py` (client), `categories.py` (data model), `sync.py` (dedupe,
  corrections, write loop), `famly.py` + `famly_mapping.py`, `notify.py`,
  `config.py`, `cli.py`.
- `tests/` - pytest against an in-process mock (`tests/conftest.py`). Nothing
  here talks to a real account. The tests do not depend on the machine's time
  zone: they compare instants, never wall-clock hours.
- `tools/` - scripts that *do* talk to the real API, for re-verification after
  an app update: `probe_write_path.py`, `roundtrip_categories.py`,
  `probe_corrections.py`, `analyze_categories.py`. Never run them without the
  user asking. `probe_corrections.py` also runs against the mock in the test
  suite, so it cannot rot.
- `examples/` - config, data files, systemd units.
- `.github/workflows/` - `ci.yml` (tests on 3.11 to 3.13) and `publish.yml`
  (PyPI upload when a release is published; see `docs/releasing.md`).

## Commands

```bash
pip install -e ".[dev]"
pytest                      # must stay green
ruff check . && ruff format --check .
```

## Rules

- **Writes are dry-run by default.** Never add a code path that writes to the
  API without `--live` / `dry_run=False` being explicit.
- **Send the complete log object.** The Napper server answers 500, not a
  validation error, when fields are missing - including the ones it
  overwrites (`createdAt`, `updatedAt`, `sortKey2`).
- **Do not trust the write response's first element** as the created entry:
  it contains every log of that day. Use the ID we generated.
- **Famly entries carry deterministic Napper IDs.** `NAMESPACE` and the key
  format (child ID, category, start as UTC seconds) in `famly_mapping.py`
  define them. Changing either gives every event a new ID and re-imports
  everything. Do not, without a migration.
- **A correction overwrites only what the source owns:** start, end, comment
  and the fields the source sets (`sync.changes`). Everything else on the
  Napper entry stays, because people add fields in the app.
- **Napper rotates tokens on every refresh.** A second holder of the same
  refresh token invalidates the first. When testing against an account that
  another client already uses, give the tool a token file with a disabled
  refresh token instead of a copy of the real one.
- **No personal data in the repo.** No baby/child/account IDs, names, e-mails,
  tokens or webhook URLs - not in code, tests, docs or commit messages. Test
  fixtures use `baby-1`, `account-1`, "Child". Commit with a GitHub noreply
  address, never a private one.
- **Secrets live in `~/.config/napper-sync/`** and are gitignored. Never read
  them into a commit or print them.
- When changing the Famly mapping, add an anonymised real event to
  `tests/test_famly_mapping.py`.
- Keep the English-only convention for identifiers, docstrings and docs.
  Two intentional exceptions: the German category aliases in `categories.py`,
  and the short German summary at the top of the README, which is there so
  that German-speaking parents find the project.

## Things that look like bugs but aren't

- `breastLeftMinutes` / `breastRightMinutes` hold **seconds**. Napper's naming.
- Comments are stored percent-encoded. `encode_comment` / `decode_comment`.
- `BED_TIME` and `WOKE_UP` use date-based IDs; one per day; a second write
  overwrites. Also intentional.
- The Famly client sends a browser-style User-Agent because Famly answers 403
  without one. Documented in `docs/famly-api.md`.
- A changed *start* time at the nursery is not corrected: it yields a new ID.
  Within ten minutes of the old entry, or overlapping it, the time-based
  duplicate check leaves the old entry alone; otherwise a second entry appears
  next to it. Known gap, documented.
- Two Famly entries of the same category within ten minutes collapse into one,
  because the duplicate check also applies to ID-bearing entries that are not
  yet in Napper. Known gap, documented. A fix must keep protecting against
  older imports that carry random IDs.
