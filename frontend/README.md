# Aegis-FI web UI (D-34 to D-37)

A Next.js + TypeScript front end over the HTTP API. Local use only; it adds no endpoint and no database access.

## Run
1. Start the database and the API (see `docs/API.md`): API on `127.0.0.1:8000`.
2. From `frontend/`:
```bash
npm install
npm run build
npm run start
```
3. Open `http://127.0.0.1:3000`, paste a bearer token (from `backend/`: `uv run python -m aegis.cli dev-token --user you --roles analyst approver auditor`, with the API's `AEGIS_API_JWT_KEY` set) and sign in.
4. Process queued runs with `uv run python -m aegis.cli worker --once --llm stub` (from `backend/`).

`AEGIS_UI_API_URL` changes the API address the same-origin proxy forwards to (default `http://127.0.0.1:8000`).

## Checks
```bash
npm run check      # type check, ESLint, unit tests
npm run build      # production build
```
`npm run lint` runs ESLint on `src`; `node scripts/lint-summary.mjs` prints findings one per line.

## Pages
- **Overview** (`/`) - KPI cards, activity area chart with time range, status donut, score histogram, evidence-quality donut, top of the queue, quick actions, live indicator.
- **Cases** (`/queue`) - score histogram, search, status filter, sortable paginated table, row menu, quick-view drawer.
- **Case** (`/cases/:id`) - report grouped by finding type, claims table (search, filter, sort, expand), status actions with confirmation, run progress, feedback, Markdown export.
- **Approvals** - pending requests; approvers decide with a reason. The requester cannot decide their own.
- **Audit** (auditor, admin) - searchable, sortable table, events-per-day chart, CSV export.
- **System** - live data flow of the nine pipeline stages, API health, recent events, database roles, interactive architecture map.
- **Settings** - theme, live-refresh interval, sidebar. Stored in this browser only.

## Keyboard
`Ctrl/Cmd K` palette, `Ctrl/Cmd B` sidebar, `/` search, `?` shortcut list, `g` then `o`, `q`, `a`, `l`, `s`, `t` to switch page, Arrow keys or `j`/`k` and Enter in tables. Shortcuts are off while typing in a field or while a dialog is open.

## Live data
Pages poll the existing endpoints (default every 10 seconds; change it in Settings; paused while the tab is hidden). This is polling, not push. Run, claim and verification figures use a sample of the 12 highest-scored cases, because the API has no aggregate endpoint.

## Structure
```
src/app/            routes; tokens.css (design tokens), globals.css (base), ui.css (controls), dash.css (shell, cards, charts, tables, system)
src/components/     Shell (frame, sign-in, shortcuts), Bits, CommandPalette, ShortcutsHelp, ScoreChart, CaseDrawer, HeaderActions
src/components/ui/  Tip, Dialog, Menu, Popover, Toast, Controls, DataTable, Kpi, AreaChart, Donut, Sparkline, BarChart, Reveal
src/lib/            api.ts, useUrlState.ts, prefs.ts, theme.ts, useCaseSample.ts, format.ts, nav.ts,
                    queue.ts / audit.ts / claims.ts / table.ts / charts.ts / timeseries.ts / aggregate.ts / flow.ts / architecture.ts (pure, tested)
```

## Design
Near-black or white surfaces, one green accent, blue, amber and violet for chart series, status colours only for status; two radii (8 px controls, 12 px cards); Geist fonts; Lucide icons; light, dark and system themes. Charts are hand-written SVG (no charting library). Motion is used for page entrance, staggered cards, sidebar width, dialogs, drawer, popovers, toasts, chart drawing and expanding rows, and is switched off for users who prefer reduced motion. See D-35 to D-37 in `decisions.md`.

## Limits
The token sits in `sessionStorage` (a development convenience; real sign-in through an identity provider is not built). The API has no evidence-content endpoint, so evidence shows as ids only. UI tests cover the pure logic only (`node --test`); the interactions were exercised by hand in a browser (see `Project_description.md`, section 12). Accessibility had a structural scan, not a full audit. LLM text shown comes from the stub until live Claude calls are enabled (D-29).
