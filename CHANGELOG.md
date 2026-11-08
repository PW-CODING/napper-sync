# Changelog

## Unreleased

- Fixed: two Famly entries of the same category within ten minutes, such as a
  meal and a piece of fruit logged five minutes apart, were merged into one and
  the second never reached Napper. Entries that carry their own ID are now
  distinct events. The time check still protects against older imports and
  hand-made entries.

## 0.1.1 - 2026-11-08

Documentation only.

- README and deployment guide install from PyPI, with a PyPI badge.
- Project banner.
- Known limits are stated plainly: the comment belongs to the nursery entry,
  same-category entries within ten minutes merge, moved start times and
  nursery-side deletions are not followed, running naps wait until they end.
- Corrected: what happens when a token refresh cannot be saved, the permissions
  of `config.toml`, the number of retries on a 429, the Windows task's logon
  requirement and a file path in the Famly notes.

## 0.1.0 - 2026-11-08

First public release.

- Napper client: OTP login, automatic token refresh, read/write/delete for all
  eleven log categories, local field validation, 429 back-off.
- Famly client and mapping: calendar endpoint, sleep/meal/diaper → Napper,
  unknown event types reported instead of dropped.
- Sync core with duplicate detection (10-minute fuzz, span overlap, one-per-day
  markers), dry-run by default.
- Corrections: Famly entries carry a deterministic Napper ID, so a later change
  at the nursery (end time, note, diaper content) updates the entry instead of
  being skipped. CSV/JSON rows may pin an `id` for the same effect.
- CSV/JSON import.
- Discord notifications; systemd units and a Windows task example.
- Documentation of both APIs as observed.
