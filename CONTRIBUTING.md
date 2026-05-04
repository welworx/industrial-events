# Contributing

All changes should go through pull requests into `main`.

Do not push directly to `main`. Use a focused branch for each conference series,
source page, or tooling change.

## Branch Names

Recommended branch names:

- `conference/<series-slug>` for a new or updated conference series.
- `conference/<series-slug>-<year>` when the branch is specific to one event edition.
- `source/<source-slug>` for overview or discovery source updates.
- `docs/<topic>` for documentation-only changes.
- `tooling/<topic>` for generator, CI, or schema changes.

Examples:

```text
conference/iclr
conference/pbzn-2026
source/pyrometallurgical-conferences
docs/license-note
tooling/calendar-validation
```

## Pull Request Scope

Keep pull requests narrow:

- One conference series per PR when possible.
- Include source URLs for changed dates or deadlines.
- Preserve deadline history when deadlines move.
- Regenerate calendars with `uv run python scripts/build_calendars.py`.
- Run the local checks before opening the PR.

## Local Checks

```powershell
uv run python scripts/build_calendars.py
uv run ruff check
uv run ruff format --check
uv run python -B -m unittest discover -s tests
```

## Maintainer Branch Protection

Protect `main` in GitHub before accepting external contributions.

Recommended setup:

- Create a branch ruleset or branch protection rule targeting `main`.
- Require a pull request before merging.
- Require the `calendars` workflow to pass.
- Block force pushes.
- Block deletions.
- Include administrators if you want the rule to apply to repository owners too.

GitHub currently recommends rulesets for controlling how people interact with
branches and tags; classic branch protection rules are also available.

