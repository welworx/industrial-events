# Automation

The repository is source-first. `public/` is generated during CI and deployment and is not committed.

## Build

```powershell
uv run python scripts/build_site.py
```

The build validates YAML, writes GitHub Pages outputs under `public/`, and refreshes generated README sections.

## Update Candidates

External agents can ask the repo which events are worth checking:

```powershell
uv run python scripts/list_update_candidates.py --series copper --series tms --pretty
```

Filters can be repeated or comma-separated:

```powershell
uv run python scripts/list_update_candidates.py --series copper,tms --event-type conference --from 2026-01-01 --to 2028-12-31 --pretty
```

The JSON response includes event file paths, source URLs to check, last checked dates, and reasons such as `missing-venue`, `date-tbd`, `stale-check`, `deadline-approaching`, or `deadline-recently-passed`.

## Suggested External Agent Flow

1. Clone the repo and create a branch.
2. Run `scripts/list_update_candidates.py` with the relevant filters.
3. Check the returned official URLs.
4. Update the referenced YAML directly or run `scripts/update_event.py`.
5. Run `uv run python scripts/build_site.py` and tests.
6. Push the branch and open a PR.

## Direct Event Updates

`scripts/update_event.py` edits one event file in place. Example:

```powershell
uv run python scripts/update_event.py events/metallurgy/copper/copper-2028.yaml --venue "Example Convention Centre" --source-url https://example.org/copper-2028 --last-checked 2026-05-07
```

Deadline updates are supported by `--deadline-type` and `--deadline-date`; when an existing deadline date changes, the previous value is kept in `history`.

## README Updates

Contributor PRs can stay YAML-only. The workflow builds and validates generated content. On `main`, the workflow can commit regenerated README sections if YAML changes made them stale.
