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

## Event Types

`event_types` is intentionally multi-valued because many events are both a conference and an exhibition or trade fair.

Example:

```yaml
event_types:
  - conference
  - exhibition
```

The build creates filter feeds under `public/calendars/event-type/` and generated event lists under `public/events/event-type/`.

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
