# The Famly API, as observed

Famly offers an [official API](https://help.famly.co/en/articles/13859542-api-access),
but only as a paid add-on for the nursery - parents cannot enable it. The web
app at `app.famly.<tld>`, however, talks to a plain REST + GraphQL backend,
and a parent's login is enough to read everything the app shows. This
document describes that internal API. It is undocumented, unsupported, and
may change without notice. Last verified: September 2026, against
`app.famly.de`; other instances are expected to behave the same but have not
been tried.

## Instances

Famly runs one backend per country. The one that applies to an account is
visible in the browser after logging in:

```js
JSON.parse(localStorage["famly.existingLogins"])[0].baseUrl   // e.g. "famly.de"
```

Known hosts: `app.famly.de`, `app.famly.co`. The host goes into
`[famly] host` in the config.

## Authentication

One header on every request:

```
x-famly-accesstoken: <token>
```

The token is a 36-character opaque string, not a JWT. It is stored in the
browser under `localStorage["famly.accessToken"]`. There is no documented
refresh flow; when it expires the API answers

```
403  {"error": "Your login session has expired", "errorCode": 1376331503, "statusCode": 403}
```

and a new token has to be copied from a fresh browser login. In practice the
token lasted for months.

The API answers 403 to requests that do not carry a browser-style
`User-Agent`; the client therefore sends the same header the web app sends.
A scripted login is not possible - the token has to come from a browser
session. Whether scripted access is acceptable under the terms that apply to
your account is for you to judge; see the disclaimer in the README.

## The calendar endpoint

The one call that delivers everything the nursery logs:

```
GET /api/v2/calendar?type=RANGE&day=<YYYY-MM-DD>&to=<YYYY-MM-DD>&childId=<id>
```

Response: a list with one block, holding `days[]`, each with `events[]`:

```json
[{"period": {...}, "days": [
  {"day": "2026-09-17", "day_localdate": "2026-09-17", "events": [
    {"title": "...", "subtitle": "...", "from": "...", "to": null,
     "embed": {...}, "behaviors": [], "originator": {...}, "invitees": [...]}
  ]}
]}]
```

The `embed` object tells the event kind:

| Shape | Meaning |
|---|---|
| `embed.type = "CHECK_IN"` / `"CHECK_OUT"` | arrival / pick-up; `from` is the time |
| `embed.type = "childAction"`, `embed.actionType = "DIAPERCHANGE"` | diaper change; `embed.diaperingType` ∈ `WET` `DIRTY` `MIXED` `DRY`; `embed.occuredOn` is the precise time; `embed.note` may hold text |
| `embed.type = "mealRegistration"` | a meal; which one is only in the `title` (e.g. `"<child>: Frühstück"`); `embed.mealItems[]` may list `{"name": ...}` |
| no `embed.type`, but `to` is set | sleep; `embed` has `startNotes`, `endNotes`, `timezone` |

Timestamps mix offsets: sleep and diaper events come as `+00:00`, meals as
local time (`+02:00`). Each carries its offset, so parsing with
`datetime.fromisoformat` and converting to local time is enough.

## GraphQL

`POST /graphql` with `{"query": ..., "variables": {...}}` and the same header.
Introspection is enabled, which is how the schema (4,000+ types) can be
explored. The useful entry points for a parent:

```graphql
{ children { results { id name { fullName } } } }
```

Sleep data also exists as `sleep.report.list(siteId, dateRange)`, but that is
the staff-side report and is not accessible with a parent login. The calendar
endpoint above is the parent's view and is what this project uses.

## Mapping to Napper

| Famly | Napper |
|---|---|
| sleep | `NAP` with start and end |
| diaper change | `CHANGED_DIAPER`, `diaperContent` from `diaperingType` |
| meal | `SOLIDS`, meal name (and items) in the comment |
| check-in / check-out | not imported |

Imported entries get a marker appended to the comment (`(Famly)` by default,
configurable) so they are recognisable in the Napper app.

Each entry is written under a deterministic Napper ID: a uuid5 of child ID,
category and start time (as UTC seconds) in a fixed namespace. The same Famly
event therefore always maps to the same Napper entry, and because Napper
overwrites on a repeated `PUT` with the same ID, a correction at the nursery -
a nap that ended later, an added note, a different diaper content - reaches
Napper on the next run. Only start, end, comment and the fields Famly sets are
overwritten; anything added to the entry in the Napper app is kept. A changed
*start* time yields a new ID; such an event is caught by the time-based
duplicate check and left alone, not corrected.

Famly events the mapping does not understand are **listed in the output**,
never dropped silently - so a nursery that starts logging temperature or
medication will be noticed, and `napper_sync/famly_mapping.py` can be
extended (the Napper side already knows every category).
