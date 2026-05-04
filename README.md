# Conference Calendars

File-based conference tracking with generated iCalendar feeds.

## Included Conferences

- International Copper Conference:
  `conferences/metallurgy/copper.yaml`
  - Recorded events: Copper 2016, Copper 2019, COPPER-COBRE 2022, and Copper 2025.
  - Upcoming event: Copper 2028, September 3-7, 2028, Cape Town, South Africa. Venue and GPS coordinates are not listed yet.
- Extraction Meeting & Exhibition:
  `conferences/metallurgy/extraction.yaml`
  - Recorded event: Extraction 2025, November 16-20, 2025, Phoenix, Arizona, USA.
- International Symposium on Nickel and Cobalt:
  `conferences/metallurgy/ni-co.yaml`
  - Recorded event: Ni-Co 2025, November 16-20, 2025, Phoenix, Arizona, USA.
- Cross-Cutting Symposia at Extraction:
  `conferences/metallurgy/extraction-cross-cutting.yaml`
  - Recorded event: Cross-Cutting Symposia at Extraction 2025, November 16-20, 2025, Phoenix, Arizona, USA.

The 2025 Extraction, Copper, Ni-Co, and Cross-Cutting records are linked through
the `extraction-2025` co-located event group.

Tracked discovery sources:

- Pyrometallurgical Conferences:
  `sources/metallurgy/pyrometallurgical-conferences.yaml`

Discovery sources help maintainers find and verify conferences. They do not
generate calendar events by themselves.

## Calendar Consumption

Use these URLs directly in a calendar client. Subscribe to the URL instead of
importing the file if you want later updates.

Main page:

- https://welworx.github.io/conferences/

All tracked conferences:

- https://welworx.github.io/conferences/calendars/all.ics

Series feeds:

- https://welworx.github.io/conferences/calendars/series/copper.ics
- https://welworx.github.io/conferences/calendars/series/extraction.ics
- https://welworx.github.io/conferences/calendars/series/extraction-cross-cutting.ics
- https://welworx.github.io/conferences/calendars/series/ni-co.ics

Interest/category feeds:

- https://welworx.github.io/conferences/calendars/category/cobalt.ics
- https://welworx.github.io/conferences/calendars/category/copper.ics
- https://welworx.github.io/conferences/calendars/category/extractive-metallurgy.ics
- https://welworx.github.io/conferences/calendars/category/metallurgy.ics
- https://welworx.github.io/conferences/calendars/category/nickel.ics
- https://welworx.github.io/conferences/calendars/category/sustainability.ics

Country feeds:

- https://welworx.github.io/conferences/calendars/country/ca.ics
- https://welworx.github.io/conferences/calendars/country/cl.ics
- https://welworx.github.io/conferences/calendars/country/jp.ics
- https://welworx.github.io/conferences/calendars/country/us.ics
- https://welworx.github.io/conferences/calendars/country/za.ics

Other feeds:

- https://welworx.github.io/conferences/calendars/domain/metallurgy.ics
- https://welworx.github.io/conferences/calendars/group/extraction-2025.ics

Machine-readable feed index:

- https://welworx.github.io/conferences/calendars/index.json

Some clients accept `webcal://` URLs. If needed, replace `https://` with
`webcal://` in the feed URL.

The matching source files are stored under `public/calendars/`.

### Disclaimer

This project makes existing public conference information easier to access. It
does not guarantee that updates, deadline extensions, cancellations, or other
changes are captured. Information can be incomplete, outdated, or wrong. Always
verify important dates and details against the official conference source.

The maintainer is not responsible for missing updates, incorrect information,
missed deadlines, travel costs, registration decisions, or any other consequence
of using these files or calendar feeds.

Generated calendar feeds include this disclaimer in their calendar metadata and
event descriptions.

## Contributing

All changes should go through pull requests into `main`. Do not push directly to
`main`; use focused branches such as `conference/<series-slug>` or
`source/<source-slug>`.

See `CONTRIBUTING.md` for branch naming, PR scope, and maintainer branch
protection guidance.

### Layout

- `conferences/<domain>/<series>.yaml` is the source of truth.
- `sources/<domain>/<source>.yaml` tracks useful discovery and overview pages.
- `public/calendars/all.ics` contains every conference event and deadline.
- `public/calendars/series/<series>.ics` contains one conference series.
- `public/calendars/category/<category>.ics` contains every series tagged with that category.
- `public/calendars/country/<country>.ics` contains events and deadlines for one event country.
- `public/calendars/domain/<domain>.ics` contains everything under one source domain folder.
- `public/calendars/group/<group>.ics` contains co-located events and their deadlines.

Use `templates/conference-series.yaml` as the starting point for a new series.
Use `templates/source-page.yaml` for overview pages that list multiple
conferences.

### Add Or Update A Conference

1. Copy `templates/conference-series.yaml` to `conferences/<domain>/<series-slug>.yaml`.
2. Fill in the series metadata and one or more events.
3. Add deadlines under each event's `deadlines` list.
4. Run:

```powershell
uv run python scripts/build_calendars.py
```

The generated `.ics` files are written to `public/calendars/`.

### Schema

Required top-level fields:

- `series`: human-readable series name.
- `slug`: stable lowercase identifier, for example `example-systems`.
- `categories`: lowercase interest tags used to generate category calendars.

Required event fields:

- `name`: event edition name.
- `start`: first event day, `YYYY-MM-DD`.
- `end`: last event day, `YYYY-MM-DD`.

Optional event fields include `country`, `city`, `venue`, `address`, `latitude`,
`longitude`, `url`, `status`, `sources`, `deadlines`, and `co_located_with`.
Coordinates must be decimal degrees and `latitude` and `longitude` must be
provided together.

Upcoming events with known dates should stay in the calendar even when the
location is incomplete. Leave `country`, `city`, `venue`, `address`, `latitude`,
and `longitude` empty when the location is unclear. If only the country is
clear, set `country` only; do not add GPS coordinates unless the exact venue or
address is known.

If a future edition has been announced but no usable date exists yet, keep it in
`events` with `status: estimated` or `status: tentative` and omit `start` and
`end`. The generator validates its sources but skips it until a calendarable date
is known.

Required deadline fields:

- `type`: lowercase deadline kind, for example `papers`, `posters`, `registration`.
- `date`: currently applicable deadline day, `YYYY-MM-DD`.

Optional fields are shown in `templates/conference-series.yaml`.

Deadline extensions should keep history instead of overwriting context. Update
the deadline's top-level `date` and `url` to the current applicable value, then
add deadline-level `history` entries for the original and changed dates. History
entries can include:

- `date`: the deadline value that was published.
- `announced`: when that deadline value was announced, if known.
- `url`: the page that published that value or extension.
- `note`: short context such as `original deadline` or `extended deadline`.

### Source Model

Conference websites are not uniform, so the data model separates event data from
provenance:

- Use top-level `website` when a conference series has a stable home, such as a
  site that keeps pages for multiple years.
- Use per-event `url` when each edition has its own site or the official site
  changes from year to year.
- Use `sources`, event-level `sources`, or deadline-level `sources` lists to
  record where a fact came from. Source URLs are included in calendar
  descriptions.
- Use deadline-level `history` lists to record deadline changes and extension
  announcement URLs. Future scanning agents can use those URLs as hints, but the
  generator itself only uses the top-level deadline `date`.
- Use event-level `co_located_with` when several conference series share one
  venue/date block, such as Extraction 2025, Copper 2025, Ni-Co 2025, and
  Cross-Cutting Symposia. This generates `group/<group>.ics` without merging the
  individual series feeds.
- Use `sources/<domain>/...` YAML files for overview pages that list many related
  conferences. These files are discovery inputs for maintainers, not generated
  calendar events by themselves.

Recommended source `type` values are:

- `series-home`: stable series or society page.
- `event-site`: site for one edition.
- `cfp`: call for papers or submission page.
- `geocode`: page or API URL used for latitude and longitude.
- `overview`: multi-conference listing.
- `venue`: venue page used for address or location details.

### Local Checks

Run the generator and tests:

```powershell
uv run python scripts/build_calendars.py
uv run ruff check
uv run ruff format --check
uv run python -B -m unittest discover -s tests
```

Install pre-commit if you want the generator to run before each commit:

```powershell
pre-commit install
```

## License

A license file is recommended before publishing this as a public repository or
accepting contributions. Without a license, default copyright rules apply and
reuse rights are unclear beyond GitHub's platform permissions.

No license has been selected yet. Choose the intended license first, then add it
as a root-level `LICENSE` file and summarize it here.
