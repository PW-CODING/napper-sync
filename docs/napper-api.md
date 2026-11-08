# The Napper API, as observed

Everything in this document was learned by reading the app's own traffic and
by probing `https://api.napper.app` with a personal account. Napper has no
public API and publishes no documentation; nothing here is official, and any
app update may change it. Last verified: September 2026, app version 6.71.

## Authentication

E-mail one-time code, exactly as in the app. No password exists.

```
POST /auth/send-otp       {"email", "useDeviceId": false, "source": "APP", "language": "en"}
POST /auth/email-login    {"email", "otp", "useDeviceId": false}
POST /auth/refresh-token  {"idToken", "refreshToken"}
```

Both login and refresh return the same envelope:

```json
{"item": {
  "idToken":      {"token": "<jwt>", "payload": {"sub": "<accountId>", "exp": 1700000000}},
  "refreshToken": {"token": "<jwt>", "payload": {"exp": 1700000000}}
}}
```

The `idToken` lives about 30 days; the `refreshToken` for months. The client
renews the idToken automatically when fewer than seven days remain, and once
more on any 401/403 before giving up.

A refresh also replaces the `refreshToken`; the old one stops working. A
refreshed pair that cannot be stored is therefore lost together with the
login, so the client checks that it can write the token file before it sends
the refresh, and stops with an error instead.

### Headers on every request

```
Authorization:   Bearer <idToken>
Source:          APP
Version:         6.71.0
Device:          <any stable UUID>
Language:        en
Locale:          en-US
Local-Timestamp: 2026-09-18T13:05:00.000+02:00
```

`Device` is generated once and stored with the tokens. The server does not
seem to validate it against anything, but the app always sends it.

## Reading

| Call | Returns |
|---|---|
| `GET /babies` | `{"items": [...]}` - profiles with `type` `BABY` or `ADULT` |
| `GET /today/{babyId}/{isoTime}` | `{"item": {"logs": [...]}}` for the day containing `isoTime` |
| `GET /logs-between-days/{babyId}/{YYYY-MM-DD}/{YYYY-MM-DD}` | `{"items": [...]}`, inclusive range |

Ranges of a month at a time work fine; a year's worth of data came back in
~20 requests without throttling.

## Writing and deleting

```
PUT    /logs/{babyId}            body: the complete log object
DELETE /logs/{babyId}/{logId}
```

A successful `PUT` answers `{"item": {"logs": [...]}}` containing **every log
of the affected day**, not only the one written. Do not take the first element
as "the created entry" - trust the ID you generated.

### The log object

```json
{
  "id":           "LOG_<uuid>_NAP",
  "sortKey":      "LOG_<uuid>_NAP",
  "partitionKey": "<babyId>",
  "type":         "LOG",
  "category":     "NAP",
  "start":        "2026-09-17T13:10:00.000+02:00",
  "end":          "2026-09-17T14:35:00.000+02:00",
  "isOpen":       false,
  "skipped":      false,
  "comment":      "",
  "pauses":       [],
  "sortKey2":     "LOG_2026-09-17T13:10:00.000+02:00",
  "createdAt":    "2026-09-17T11:10:00.000Z",
  "updatedAt":    "2026-09-17T11:10:00.000Z"
}
```

plus the category-specific fields listed below.

* `createdAt`, `updatedAt` and `sortKey2` are overwritten by the server, but
  **must be present**. An incomplete body yields `500 Internal Server Error`,
  not a validation message. The only validation the server does up front is
  `422 MISSING_BODY_PROPERTIES: category`.
* Point events (diaper, bottle, temperature, …) still carry `end`, equal to `start`.
* `id` and `sortKey` are the primary key. A second `PUT` with the same `id`
  **overwrites** instead of duplicating - which doubles as an idempotency handle.
  napper-sync relies on this: entries from Famly get a deterministic ID (see
  `docs/famly-api.md`), so a correction is a plain re-write.
* `pauses` are interruptions within a nap or nursing session, as epoch
  milliseconds; nursing pauses additionally carry `breast`:
  `[{"start": 1762582356992, "end": 1762582357993, "breast": "LEFT"}]`.
  This project always writes an empty list.

### ID schemes

| Category | ID | Consequence |
|---|---|---|
| `BED_TIME`, `WOKE_UP` | `LOG_<YYYY-MM-DD>_<CATEGORY>` | at most one per day |
| everything else | `LOG_<uuid>_<CATEGORY>` | any number per day |

Older entries in real data sometimes lack the `_<CATEGORY>` suffix; writing
with the suffix works for every category.

### Comments are percent-encoded

A comment typed as `Kürbis & Fleisch` is stored as
`K%C3%BCrbis%20%26%20Fleisch` and comes back the same way. Encode on write,
decode on read (`urllib.parse.quote(text, safe="")` / `unquote`).

## Categories and their fields

Observed across ~5,000 entries from one account. The app may support values
not listed here.

| Category | Kind | Fields |
|---|---|---|
| `NAP` | span | `howBabySlept`, `howBabyWoke`, `babyMoodOnSleep[]`, `babyMoodOnWakeUp` |
| `BED_TIME` | point, one/day | `startMinutesAfterMidnight`, `howBabySlept`, `babyMoodOnSleep[]` |
| `WOKE_UP` | point, one/day | `howBabyWoke`, `babyMoodOnWakeUp` |
| `NIGHT_WAKING` | span | `babyMoodOnWakeUp` |
| `NURSING` | span | `breastLeftMinutes`, `breastRightMinutes`, `activeBreast` |
| `BOTTLE` | point | `bottleFeedingType`, `feedingAmount` |
| `SOLIDS` | point | `feedingAmount` |
| `PUMPING` | point | `amountPumpedLeft`, `amountPumpedRight` |
| `CHANGED_DIAPER` | point | `diaperContent` |
| `TEMPERATURE` | point | `temperature` (float, °C) |
| `MEDICINE` | point | - (drug name goes into `comment`) |

Enum values:

| Field | Values |
|---|---|
| `diaperContent` | `DRY` `WET` `DIRTY` `MIXED` |
| `bottleFeedingType` | `FORMULA` `BREAST_MILK` |
| `activeBreast` | `LEFT` `RIGHT` |
| `howBabySlept` | `IN_BED` `NEXT_TO_ME` `NURSING` `WORN_OR_HELD` `SWING` `STROLLER` `CAR` |
| `howBabyWoke` | `WOKE_UP_NATURALLY` `WOKE_UP_CHILD` |
| `babyMoodOnSleep` | list of `LONG_TIME_TO_FALL_ASLEEP`, `UPSET` |
| `babyMoodOnWakeUp` | integer 1–5 |

### Three traps

1. **`breastLeftMinutes` and `breastRightMinutes` are seconds.** A nursing
   session from 05:06:54 to 05:12:39 is stored as `346`. The field name lies.
2. **`startMinutesAfterMidnight`** on `BED_TIME` is `0` unless the child went
   to bed after midnight; then it is the minutes past midnight.
3. **Day markers overwrite.** Writing a `BED_TIME` for a day that already has
   one silently replaces it - that is what the date-based ID means.

## Rate limiting

About ten writes within ten seconds trigger

```json
{"name": "HttpError", "statusCode": 429, "message": "Too many requests - 10 seconds"}
```

The client parses the number of seconds, waits, and tries again, up to four attempts
in all. It waits at most 60 seconds; when the server asks for longer, the call
fails with an error at once instead of blocking the run.
Reads were never throttled during testing.

## How the write endpoint was found

Neither of the two public reverse-engineering projects
([napper-export](https://github.com/brittraee/napper-export),
[home-assistant-napper](https://github.com/g-nogueira/home-assistant-napper))
writes, so the path had to be discovered.

Seventeen candidate paths were probed with a deliberately unusable body
(`{"__probe__": true}`). A rejected request creates nothing, so this is safe.
Sixteen answered 404; `PUT /logs/{babyId}` answered
`422 MISSING_BODY_PROPERTIES: category`.

The body shape was harder because incomplete bodies yield 500 instead of a
hint. Complete shapes modelled on a real log object were tried in order; the
very first - the full object as read back - was accepted. `DELETE
/logs/{babyId}/{logId}` was found the same way while cleaning up the test
entry.

`tools/probe_write_path.py` repeats this procedure, for the day an app update
moves the endpoint.

## What has been verified

* OTP login, token storage, automatic refresh.
* All eleven categories: written, read back with every field and the comment
  intact, deleted again, day verified empty.
* Non-ASCII comments round-trip (`Kürbis & Fleisch`).
* 429 back-off path exercised against the real API.
* Months of unattended hourly syncing on a Raspberry Pi.
* Corrections (2026-11-08, `tools/probe_corrections.py`): an entry under a
  uuid5-based ID is accepted and reads back; a second `PUT` with the same ID
  replaces it instead of duplicating; a field added in the app survives when
  the object is sent back with it; an unchanged repeat writes nothing.

Not verified: writing open entries (`isOpen: true`) and `pauses`.
