# CLAUDE.md

Source-first tracker for industrial conferences and trade fairs. YAML under `events/` is the source of truth; `public/` and the generated README sections are build output. Full schema: `docs/data-model.md`. Workflow and PR rules: `CONTRIBUTING.md`.

## Layout

```text
events/<domain>/<series-slug>/metadata.yaml        series (slug must equal folder name)
events/<domain>/<series-slug>/<slug>-<year>.yaml   one edition
events/<domain>/<series-slug>/<slug>-<year>.program.yaml   optional sessions/talks for that edition
sources/<domain>/<source>.yaml                     discovery pages (not events)
```

Domains today: `energy`, `materials`, `metallurgy`, `mining`, `process-engineering`.

## Adding or updating an event (fast path)

1. `ls events/*/ | grep -i <name>` to see whether the series exists. Never create a duplicate series.
2. Verify on the **official** site first (organiser page, not aggregators). Aggregators (showsbee, 10times, tradefairdates) are only leads; say so in the source `note` if you used one.
3. Existing edition: `uv run industrial-events-update-event --help` edits one file in place (dates, venue, deadlines, sources). New edition: copy the previous year's file, change fields.
4. Run the checks below. Commit the regenerated `README.md` together with the YAML.

To find what to refresh: `uv run industrial-events-list-candidates --series <slug> --pretty`.

## Event file rules (learned from validation failures)

- Required: `name`, `event_types` (`conference`, `exhibition`, `trade-fair`, ...), `start`, `end` (`YYYY-MM-DD`, quoted). Dateless events need `status: estimated|tentative`.
- Also set `timezone` (IANA), `city`, `country` (ISO-2), `venue`, `status`.
- Use ASCII transliteration in city/venue (`Dusseldorf`, `Duesseldorf`), matching existing files.
- Name pattern: `<SERIES> <year>, <ordinal> <full title>` (e.g. `EMC 2027, 14th European Metallurgical Conference`). Derive the ordinal from the previous edition; ordinals must stay consistent.
- **Top-level `url` must not equal any `sources[].url`** (test `test_event_top_level_urls_do_not_duplicate_evidence_sources`) and must be unique across events (`industrial-events-check-duplicate-event-urls`). If the only page you have is the evidence page, omit `url`; the series `website` is used as fallback.
- Every fact should be backed by a `sources` entry: `type` (`event-site`, `overview`, `program`, `cfp`, ...), `scope: event`, `url`, `last_checked` (today's date, quoted), and a one-sentence `note` quoting what the page says. Do not bump `last_checked` on pages you did not actually open.
- Deadlines: `deadlines:` list with `type`, `name`, `date`, `url`, `status`, `sources`. When a deadline moves, keep history in `history:` instead of overwriting. Do not invent deadlines; omit if the date is not published.
- Co-located events share a group: copy the `co_located_with` block (`group`, `name`, `url`, `series`) from a sibling. The Dusseldorf fair week uses `group: bright-world-of-metals-2027` (METEC, GIFA, THERMPROCESS, NEWCAST, plus ESTAD and EMC).
- Unknown YAML keys are rejected. Keep dates identical across co-located siblings unless the organiser publishes different ones (EMC 2027 is 20-23 June, the fair is 21-25 June).

## Series metadata

`series`, `slug`, `description`, `recurrence` (`recurring`/`one-off`/`unknown`), `categories`, optional `website`, `topics`, `sources`. Add one `series-home` source pointing to the organiser page.

## Programs (`*.program.yaml`)

Only add when an official schedule exists. Filename must be `<edition>.program.yaml` next to the event file; `series` = series slug, `edition` = file stem, IANA `timezone`, `sources` with at least one `type: program`, and `sessions` (unique slug `id`, `title`, `type`, `date` inside event dates, quoted `"HH:MM"` `start`/`end`). Rooms cannot overlap in time; a roomless session blocks all rooms. Talks need `title` and `authors`. See `events/metallurgy/lead-zinc/lead-zinc-2026.program.yaml` as a model. Unquoted times (`10:35`) are rejected.

## Checks (run before every commit)

```bash
uv run industrial-events-build-site            # validates YAML, regenerates README sections
uv run ruff check && uv run ruff format --check
uv run python -B -m unittest discover -s tests
uv run industrial-events-check-duplicate-event-urls
uv run industrial-events-check-yaml-links      # network; may fail behind the sandbox proxy
```

`UV_NATIVE_TLS` deprecation warnings are noise. Do not commit `public/`.

## Verification tips

- Official site JSON-LD can be off by a day (METEC 2027 shows 20-24 June in JSON-LD but 21-25 June on the page). Prefer the visible text and a second source.
- In this sandbox some hosts return 502 via the proxy; fall back to the organiser's society page (e.g. `vdeh.de`, `gdmb.de`, `aist.org`) or a university/Fraunhofer record for historical editions.
- Historical editions: record venue and dates only when two independent sources agree, and say in `note` where they conflict.

## Git

Develop on the branch named in the session instructions, commit YAML + regenerated README together, push with `git push -u origin <branch>`. Open a PR only when asked.
