# Automation

The repository is source-first. `public/` is generated during CI and deployment and is not committed.

## Build

```powershell
uv run industrial-events-build-site
```

The build validates YAML, writes static site outputs under `public/`, and refreshes generated README sections.

## Cloudflare Pages

GitHub Actions builds and validates the site but does not deploy it to GitHub Pages by default.
Cloudflare Pages should build the repo directly.

Suggested Cloudflare Pages settings:

- **Framework preset**: None
- **Build command**:

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh && ~/.local/bin/uv python install 3.12 && ~/.local/bin/uv run industrial-events-build-site --skip-readme
  ```

- **Build output directory**: `public`
- **Environment variable**: set `INDUSTRIAL_EVENTS_SITE_URL` to the canonical Cloudflare Pages or custom-domain URL, including `https://`.

`site.yaml` still contains the previous GitHub Pages URL as a fallback. That makes it possible
to switch back later; re-enable GitHub Pages by setting the repository variable
`ENABLE_GITHUB_PAGES` to `true`.

## Update Candidates

External agents can ask the repo which events are worth checking:

```powershell
uv run industrial-events-list-candidates --series copper --series tms --pretty
```

Filters can be repeated or comma-separated:

```powershell
uv run industrial-events-list-candidates --series copper,tms --event-type conference --from 2026-01-01 --to 2028-12-31 --pretty
```

The JSON response includes event file paths, source URLs to check, last checked dates, and reasons such as `missing-venue`, `date-tbd`, `stale-check`, `deadline-approaching`, or `deadline-recently-passed`.

## Suggested External Agent Flow

1. Clone the repo and create a branch.
2. Run `industrial-events-list-candidates` with the relevant filters.
3. Check the returned official URLs.
4. Update the referenced YAML directly or run `industrial-events-update-event`.
5. Run `uv run industrial-events-build-site` and tests.
6. Push the branch and open a PR.

## Direct Event Updates

`industrial-events-update-event` edits one event file in place. Example:

```powershell
uv run industrial-events-update-event events/metallurgy/copper/copper-2028.yaml --venue "Example Convention Centre" --source-url https://example.org/copper-2028 --last-checked 2026-05-07
```

Deadline updates are supported by `--deadline-type` and `--deadline-date`; when an existing deadline date changes, the previous value is kept in `history`.

## Link Checks

New YAML files should not introduce dead links:

```powershell
uv run industrial-events-check-yaml-links
```

The command checks staged, newly added `.yaml` and `.yml` files by default. A weekly GitHub Actions workflow also checks links attached to event records from the previous calendar year onward:

```powershell
uv run industrial-events-link-targets --years-back 1 --output build-artifacts/recent-event-links.md
```

The weekly workflow sends those targets to lychee and creates or updates one GitHub issue when invalid links need deeper review. Older historical event links are intentionally excluded from the scheduled check.

For an initialization pass, manually dispatch the link-check workflow with `scope` set to `all`. That checks historical event links too, while later scheduled runs keep the narrower recent-event scope.

## README Updates

Contributor PRs can stay YAML-only. The workflow builds and validates generated content. On `main`, the workflow can commit regenerated README sections if YAML changes made them stale.
