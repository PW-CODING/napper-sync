# Changelog

## 0.1.1 - 2026-11-08

Documentation only.

- README and deployment guide install from PyPI, with a PyPI badge.
- Project banner.

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
