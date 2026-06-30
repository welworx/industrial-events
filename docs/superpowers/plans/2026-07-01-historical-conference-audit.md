# Historical Conference Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audit every conference series in `events/`, research historical editions and source-backed series descriptions, and add missing YAML records without weakening validation quality.

**Architecture:** Treat each conference series as a source-backed data maintenance unit. Build an inventory first, then dispatch research batches by domain and series density, with one implementation/review loop per batch. Store only verified facts in YAML and keep uncertain findings in notes or discovery-source records until corroborated.

**Tech Stack:** Python 3.12, `uv`, PyYAML, repo CLIs (`industrial-events-build-site`, `industrial-events-check-duplicate-event-urls`, `industrial-events-check-yaml-links`), pytest, web search/browser research, optional Python Playwright for blocked pages.

---

## Baseline

Current branch: `research/historical-conference-audit`

Current catalog snapshot:

- 65 series metadata files under `events/<domain>/<series>/metadata.yaml`
- 403 edition YAML files
- All metadata files currently have a non-empty `description` by schema, so the description work is quality/completeness review, not only blank-field filling
- `uv.toml` already contains `cache-dir = ".uv-cache"`

Primary data rules:

- Stable series facts go in `metadata.yaml`
- Edition facts go in `<series-slug>-<year>.yaml`
- Prefer official event, society, organizer, proceedings, or archive pages over third-party listings
- Do not invent dates, venues, coordinates, deadlines, or edition names
- Use `sources` with `last_checked` for every newly added or corrected fact
- If a site blocks fetches, record only facts that can be verified through an accessible authoritative source

## Files

- Modify: `events/**/metadata.yaml`
- Create or modify: `events/**/*.yaml`
- Optionally create or modify: `sources/**/*.yaml`
- Generated during verification only: `public/**`, `README.md`, `public/events.xml`, calendar indexes if the build updates them
- Do not intentionally modify unrelated source code unless validation gaps block the data work

## Research Sources And Fetch Strategy

Use this source priority order for each series:

1. Official current event page, organizer page, society page, or event archive
2. Official proceedings/program PDFs or abstract books
3. Publisher, university, association, or conference-management archive pages
4. Internet Archive snapshots of official pages
5. Trusted industry calendars only as discovery leads or tie-breakers, not sole authority when official facts conflict

Search patterns per series:

- `"<Series Name>" conference archive`
- `"<Series Name>" proceedings <year>`
- `"<Series Name>" program <year>`
- `site:<organizer-domain> <series slug> <year>`
- `"<Edition Name>" "<city>" "<year>"`
- `"<Series Acronym>" "<year>" "proceedings"`

Blocked web-fetch fallback:

- If the web/browser fetch is blocked by robots, JavaScript, WAF, expired TLS, or 403/429, retry with the repository Playwright workaround from `AGENTS.md` only when needed.
- If shell network access is blocked, request sandbox escalation for the same Playwright or download command instead of changing cache paths.
- If Google-style search is needed, use the available web search interface with targeted queries. Do not rely on unsourced search snippets for YAML facts.
- If only a snippet is visible, use it to locate another accessible authoritative source.
- If a PDF is blocked but indexed elsewhere, look for mirrored official proceedings pages, DOI/Crossref metadata, society pages, or library records.
- Mark a candidate as unresolved rather than adding a weak record.

## Success Criteria

- Every one of the 65 series has been inspected.
- Each inspected series has an evidence note in the controller log or batch summary stating: researched sources, known historical coverage, added editions, skipped candidates, and reason for uncertainty.
- Missing historical editions are added only when at least one authoritative source verifies name and year, and dated records have start/end if available.
- Series descriptions are source-backed, specific, and useful for users scanning the catalog.
- New or changed YAML passes project validation.
- Link checks run for newly added/changed YAML where practical; blocked links are marked with `url_status` or source notes only when justified.
- Final verification passes:

```powershell
uv run industrial-events-build-site
uv run industrial-events-check-duplicate-event-urls
uv run pytest
git diff --check
```

## Task 1: Inventory And Batch Map

**Files:**
- Create: `docs/superpowers/plans/2026-07-01-historical-conference-audit.md`
- Read: `events/**/metadata.yaml`
- Read: `docs/data-model.md`

- [ ] **Step 1: Confirm branch and clean state**

Run:

```powershell
git status -b --short
```

Expected: branch is `research/historical-conference-audit`; no unexpected dirty files except the plan while it is being written.

- [ ] **Step 2: Generate the series inventory**

Run:

```powershell
uv run python -c 'from pathlib import Path; import yaml; print("path,series,recurrence,editions,first,last");\
for meta in sorted(Path("events").glob("*/*/metadata.yaml")):\
 d=yaml.safe_load(meta.read_text(encoding="utf-8")) or {}; editions=sorted(p.stem for p in meta.parent.glob("*.yaml") if p.name != "metadata.yaml");\
 print(",".join(map(str,[str(meta.parent).replace("\\", "/"), d.get("series",""), d.get("recurrence",""), len(editions), editions[0] if editions else "", editions[-1] if editions else ""])))'
```

Expected: 65 series rows and 403 edition files at baseline.

- [ ] **Step 3: Classify batches**

Use the inventory to group series into batches:

- Batch A: sparse energy/materials series with 0-3 editions
- Batch B: dense materials series with known archives
- Batch C: metallurgy long-running society/congress series
- Batch D: mining/process-engineering series
- Batch E: cleanup pass across all descriptions and source entries

- [ ] **Step 4: Commit the plan**

Run:

```powershell
git add docs/superpowers/plans/2026-07-01-historical-conference-audit.md
git commit -m "Plan historical conference audit"
```

Expected: one plan commit on `research/historical-conference-audit`.

## Task 2: Sparse Series Research Pass

**Files:**
- Modify: `events/energy/**/metadata.yaml`
- Modify: `events/energy/**/*.yaml`
- Modify: `events/materials/**/metadata.yaml`
- Create or modify: sparse `events/materials/**/*.yaml`
- Optionally create or modify: `sources/energy/**/*.yaml`, `sources/materials/**/*.yaml`

- [ ] **Step 1: Dispatch one implementer subagent for sparse series**

Provide the subagent the full list of sparse series from the inventory, the data model, and these rules:

- Research every assigned series with source priority order from this plan.
- Add historical editions only when sources verify at least name and year.
- Prefer exact dates and venues when official pages/proceedings provide them.
- Add or improve `description` only with source-backed wording.
- Do not add coordinates unless a source provides them or a geocode source is explicitly recorded.

- [ ] **Step 2: Validate after the subagent returns**

Run:

```powershell
uv run industrial-events-build-site
uv run industrial-events-check-duplicate-event-urls
uv run pytest
git diff --check
```

Expected: all commands pass.

- [ ] **Step 3: Link-check changed sparse YAML**

Run one command per changed YAML file or folder:

```powershell
uv run industrial-events-check-yaml-links path/to/changed.yaml
```

Expected: active links pass, or blocked/restricted links are documented with `url_status`/source notes where appropriate.

- [ ] **Step 4: Review and commit**

Use a spec reviewer subagent first, then a code/data quality reviewer subagent. Commit only after both approve.

## Task 3: Dense Materials Series Research Pass

**Files:**
- Modify: `events/materials/aluminium-china/**`
- Modify: `events/materials/aluminium-exhibition/**`
- Modify: `events/materials/calphad/**`
- Modify: `events/materials/challenging-glass/**`
- Modify: `events/materials/glass-problems-conference/**`
- Modify: `events/materials/glassbuild-america/**`
- Modify: `events/materials/icg-annual-meeting/**`
- Modify: `events/materials/unitecr/**`
- Modify: `events/materials/world-foundry-congress/**`
- Optionally create or modify: `sources/materials/**/*.yaml`

- [ ] **Step 1: Dispatch one or more material-series subagents**

Split by likely archive type:

- Exhibition/trade fair archives: aluminium, glassbuild, glasstec, vitrum
- Society/technical conference archives: CALPHAD, Challenging Glass, Glass Problems, ICG, UNITECR, World Foundry Congress

- [ ] **Step 2: Require batch summaries**

Each subagent must report:

- Series inspected
- Sources used
- Editions added or corrected
- Known gaps left unresolved
- Fetch-blocked pages and fallback attempts

- [ ] **Step 3: Validate, link-check, review, commit**

Use the same commands and two-stage review gate as Task 2.

## Task 4: Metallurgy Series Research Pass

**Files:**
- Modify: `events/metallurgy/**/metadata.yaml`
- Create or modify: `events/metallurgy/**/*.yaml`
- Optionally create or modify: `sources/metallurgy/**/*.yaml`

- [ ] **Step 1: Split metallurgy into subagent batches**

Use these groups:

- Long-running international congresses: `infacon`, `molten-slags`, `copper`, `metec`
- Society recurring meetings: `com`, `emc`, `tms`, `aistech`, `gifa`
- SAIMM/TMS/technical symposia: `base-metals`, `ni-co`, `pgm`, `saimm-pyrometallurgy`, `lead-zinc`, `lmpc`
- Slag, sulfur, steel, silicon series: `global-slag`, `euro-slag`, `slag-valorisation`, `sulphur-and-sulphuric-acid`, `eec`, `eases`, `steelsim`, `silicon`

- [ ] **Step 2: Research and update**

Each subagent must search official organizer, proceedings, program, and archive pages before third-party sources. Add missing historical editions only when sourced.

- [ ] **Step 3: Validate, link-check, review, commit**

Use the same commands and two-stage review gate as Task 2.

## Task 5: Mining And Process Engineering Research Pass

**Files:**
- Modify: `events/mining/**/metadata.yaml`
- Create or modify: `events/mining/**/*.yaml`
- Modify: `events/process-engineering/**/metadata.yaml`
- Create or modify: `events/process-engineering/**/*.yaml`
- Optionally create or modify: `sources/mining/**/*.yaml`, `sources/process-engineering/**/*.yaml`

- [ ] **Step 1: Split mining/process series**

Use these groups:

- Large congresses: `impc`, `sme-annual`, `apcom`
- SAIMM/mineral-processing series: `heavy-minerals`, `alta`, `emprc`, `minproc`
- Process-engineering archive: `cfd-minerals`

- [ ] **Step 2: Research and update**

Prioritize official congress archives, proceedings pages, society event pages, and conference programs.

- [ ] **Step 3: Validate, link-check, review, commit**

Use the same commands and two-stage review gate as Task 2.

## Task 6: Description And Source Quality Pass

**Files:**
- Modify: `events/**/metadata.yaml`
- Modify: `sources/**/*.yaml`

- [ ] **Step 1: Audit descriptions**

For every `metadata.yaml`, check that `description` states what the series is, its technical/industry scope, and the organizer/context when source-backed. Avoid marketing copy and unsupported claims.

- [ ] **Step 2: Audit source entries**

Ensure new/changed source entries have:

- `type`
- `scope` where useful
- `url`
- `last_checked: "2026-07-01"` or the actual research date
- `note` only when it adds useful evidence context

- [ ] **Step 3: Validate, review, commit**

Run full verification and use the two-stage review gate.

## Task 7: Final Whole-Catalog Verification

**Files:**
- Read/verify: whole repository

- [ ] **Step 1: Run full build and tests**

Run:

```powershell
uv run industrial-events-build-site
uv run industrial-events-check-duplicate-event-urls
uv run pytest
git diff --check
```

Expected: all pass.

- [ ] **Step 2: Run targeted link checks**

Run `industrial-events-check-yaml-links` on every newly added YAML file and every metadata file whose `website` or `sources` changed.

- [ ] **Step 3: Update graph**

Run:

```powershell
uv run graphify update .
```

Expected: graph update completes, or any graphify/network/cache failure is reported separately without blocking valid catalog data changes.

- [ ] **Step 4: Final review**

Dispatch a final reviewer over the whole diff with instructions to check:

- Unsupported facts
- Source/date mismatches
- Duplicate event URLs
- Bad series split decisions
- Schema drift
- Generated-file consistency

- [ ] **Step 5: Final commit or branch handoff**

Commit any final generated updates and prepare a concise summary with:

- Series inspected
- Editions added
- Descriptions improved
- Sources added
- Known unresolved historical gaps
- Verification commands and results
