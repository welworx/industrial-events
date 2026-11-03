# Data Model

Source event data lives under `events/<domain>/<series-slug>/`.

```text
events/metallurgy/copper/
  metadata.yaml
  copper-2025.yaml
  copper-2028.yaml
```

## Series Metadata

`metadata.yaml` describes the event series or tracked event collection.

Required fields:

- `series`: human-readable name.
- `slug`: stable lowercase slug matching the folder name.
- `description`: short explanation of what the series represents.
- `recurrence`: `recurring`, `one-off`, or `unknown`.
- `categories`: broad filter tags.

Optional fields:

- `website`: stable series website, if one exists.
- `topics`: narrower technical tags.
- `sources`: stable sources used to verify the series.

## Event Files

Each event edition or one-off event is stored as one YAML file in the series folder.

Required fields:

- `name`: event name.
- `event_types`: one or more of tags such as `conference`, `exhibition`, or `trade-fair`.

Dated events also require:

- `start`: first event day, `YYYY-MM-DD`.
- `end`: last event day, `YYYY-MM-DD`.

Date-TBD events may omit `start` and `end` only when `status` is `estimated` or `tentative`.

Common optional fields:

- `timezone`
- `city`
- `country`
- `venue`
- `address`
- `latitude`
- `longitude`
- `url`
- `status`
- `sources`
- `deadlines`
- `co_located_with`

Edition-specific websites belong in the event file as `url`. The builder falls back to the series `website` only when the event has no `url`.

## Regional Editions

Some event brands run recurring regional editions. Prefer one series folder per stable
recurring edition when that is the useful maintenance and filtering unit, for example
`events/metallurgy/copper/` for Copper editions.

Glassman is currently an exception. `events/materials/glassman/` groups Glassman Asia,
Glassman Europe, and Glassman Latin America in one parent series folder. Research found
that these are separate recurring regional editions with historical continuity, so a future
cleanup may split them into `glassman-asia`, `glassman-europe`, and
`glassman-latin-america`. Leave the current grouping in place until that migration is
done deliberately.

## Event Types

`event_types` is intentionally multi-valued because many events are both a conference and an exhibition or trade fair.

Example:

```yaml
event_types:
  - conference
  - exhibition
```

The build creates filter feeds under `public/calendars/event-type/` and generated event lists under `public/events/event-type/`.

## Programs

A technical program (sessions and talks) is stored next to the event it belongs to, as `<edition>.program.yaml`:

```text
events/metallurgy/lead-zinc/
  lead-zinc-2026.yaml
  lead-zinc-2026.program.yaml
```

The build treats `*.program.yaml` as program data, not as an event, and fails when a program file has no event file with the same name. When a program file exists, the README shows a 📋 icon for the event that links to it. The icon is separate from the `program` source type, which links to an official external program page (🗓).

Top-level fields, all required: `series` (must equal the series slug), `edition` (must equal the file name), `timezone`, `sources` and `sessions`. `sources` is required and records where the program came from (official page or PDF). Every source needs `type` and `last_checked`, and at least one source must have `type: program`. `timezone` is required (the event location is always known) and must be a real IANA name such as `Asia/Tokyo`; session and talk times are local to it.

Each session requires `id` (unique lowercase slug), `title`, `type` (`oral`, `plenary`, `keynote`, `panel`, `poster`, `workshop`, `tutorial`, `short-course`, `ceremony` or `social`), `date` (within the event dates), `start` and `end`. Optional: `room`, `room_detail`, `track`, `note`, `chairs` (`name`, `affiliation`), `chairs_truncated` and `talks`. Breaks are not recorded. A session may have no talks (for example an opening address, or a block the organiser lists as TBA). Two sessions in the same room may not overlap in time (room names are compared ignoring case and spacing); a session without a room occupies every room. `chairs_truncated: true` means the organiser's source cuts off the last chair's affiliation (for example "JX Advanced"), so the stored text is incomplete.

Each talk requires `title` and `authors` (`name`, `presenting`, `affiliations`). Optional: `paper_id` (string, unique within the edition), `start` and `end` (together; inside the session and not overlapping the previous talk), `abstract`, `abstract_url`, `doi` and `keywords`.

A program needs a dated event: program files for events without `start` and `end` are rejected. Times are quoted `"HH:MM"` strings in the event's local time. Unquoted times such as `10:35` are read as numbers by YAML and are rejected.

## Sources

Use source entries to record where a fact came from.

```yaml
sources:
  - type: event-site
    scope: event
    url: https://example.org/event-2027
    last_checked: "2026-05-07"
```

Recommended `type` values include `series-home`, `event-site`, `cfp`, `overview`, `venue`, and `geocode`.

## Validation

The build rejects unknown fields, invalid slugs, invalid dates, duplicate series slugs, incomplete coordinate pairs, and dated events with only one of `start` or `end`.
