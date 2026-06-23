# Contributing

All changes should go through pull requests into `main`. Keep content PRs focused and prefer YAML-only changes under `events/` or `sources/`.

## Branch Names

Recommended branch names:

- `event/<series-slug>` for a new or updated event series.
- `event/<series-slug>-<year>` for one event edition.
- `source/<source-slug>` for overview source updates.
- `docs/<topic>` for documentation-only changes.
- `tooling/<topic>` for generator, CI, or schema changes.

## Pull Request Scope

- One event series or one source page per PR when possible.
- Include official source URLs for changed dates, venues, or deadlines.
- Preserve deadline history when deadlines move.
- Do not commit `public/`; it is generated during CI and deployment.

## Local Checks

```powershell
uv run industrial-events-build-site
uv run industrial-events-list-candidates --pretty
uv run industrial-events-check-yaml-links
uv run ruff check
uv run ruff format --check
uv run python -B -m unittest discover -s tests
```

## Data Model

See [docs/data-model.md](docs/data-model.md) for the YAML layout and schema rules.

## Maintainer Branch Protection

Protect `main` before accepting external contributions:

- Require pull requests before merging.
- Require the `Build site` workflow to pass.
- Block force pushes.
- Block deletions.
