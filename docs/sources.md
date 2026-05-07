# Discovery Sources

Discovery sources are pages that help discover or verify events but are not event records themselves.

Examples:

- pages listing many related conferences,
- society or organizer event hubs,
- society pages with multiple event series,
- long-lived archive pages,
- industry calendars useful for future checks.

Store them under `sources/<domain>/<source-slug>.yaml`.

Required fields:

- `name`
- `slug`
- `url`
- `type`

Optional fields:

- `categories`
- `topics`
- `last_checked`
- `last_updated`
- `note`

Discovery sources do not generate calendar events directly. They are used for discovery, README source list, and future update automation.
