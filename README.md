# kalshibot3

A personal sports-betting workbook for [Kalshi](https://kalshi.com), with an AI brain attached.

Three independent layers:

1. **Workstation** — Better Kalshi frontend. View markets, place limit orders, manage positions. Useful day one with no AI.
2. **Logbook** — Every bet auto-tagged across multiple dimensions (source, strategy, timing, conviction, outcome) and filterable. Builds a longitudinal record of decisions and results.
3. **AI partner** — Proactively scans news, odds movements, and live game state to suggest specific bets with Kelly sizing and justification. Discusses suggestions in chat. Logs the reasoning behind every decision.

Soccer ships first for the 2026 World Cup. NFL slots in next via a sport plugin — the dashboard, AI, ledger, and Kalshi plumbing are sport-agnostic.

## Architecture (one-line)

Monolith: Python 3.12 / FastAPI backend + React 19 / TypeScript dashboard + SQLite. Single process. Bound to `127.0.0.1` only.

```
backend/   FastAPI app, Kalshi client, SQLAlchemy models, strategy logic
dashboard/ Vite + React + Tailwind v4 + TanStack Query
docs/      Brainstorms, plans, reference material
```

See `docs/plans/` for the implementation plan, and `CLAUDE.md` for design conventions.

## This is a single-user, local-only app

The dashboard binds to `127.0.0.1:8000` and has no authentication — OS-level access to the machine running it *is* the authentication. If you clone this repo and want to use it, you need:

- Your own Kalshi API key + RSA private key
- Your own API-Football and Odds API subscriptions
- Your own LLM API key (once Phase 4 ships)

Drop them in `.env` (see `.env.example`). Nothing in the repo holds personal data. `.env`, `*.pem`, the SQLite database, and the `data/` directory are all gitignored.

## Setup

> Phase 1 in progress. This section will fill out as chunks land.

```bash
git clone https://github.com/Zyori/kalshibot3.git
cd kalshibot3
cp .env.example .env
# fill in .env

./start.sh
```

`start.sh` enforces single-instance: stale processes are killed, a PID lockfile prevents concurrent launches, and the launch verifies only one bot process exists. This is non-negotiable — running two instances against Kalshi at once will double every position. (Lesson learned the expensive way in V1.)

## Status

| Phase | Scope | State |
|-------|-------|-------|
| 1 | Foundation: project bootstrap, DB layer, Kalshi auth + REST | In progress |
| 2 | Workstation: place/manage orders end-to-end | Not started |
| 3 | Logbook: auto-tagged bet history + ledger UI | Not started |
| 4 | AI Partner: proactive suggestions, chat, alerts | Not started |
| 5 | Polish: error states, sound prefs, single-instance hardening | Not started |

See `docs/plans/2026-05-25-001-feat-kalshi-betting-assistant-dashboard-plan.md` for the full plan.

## License

Personal use. No license granted for redistribution at this time.
