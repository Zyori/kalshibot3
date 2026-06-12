# kalshibot3

A personal sports-betting workbook for [Kalshi](https://kalshi.com) with an AI trading partner wired into it.

Not a bot. There's no autonomous trading and never will be — every order is staged, then confirmed by a human. It's a cockpit: a faster Kalshi frontend, a ledger that remembers *why* you made every bet, and a Claude session sitting next to you that reads the same live game state you do and stages calls you confirm with one click.

Built to trade the **2026 World Cup**. Soccer ships first; the dashboard, ledger, AI, and Kalshi plumbing underneath are sport-agnostic — NFL slots in as a plugin.

## Three layers

1. **Workstation** — A better Kalshi frontend. Live + upcoming markets with cross-book odds, one-click limit orders, position cards with realized/unrealized P&L. Useful day one with zero AI.
2. **Logbook** — Every bet auto-tagged across source, strategy, timing, conviction, and outcome, then filterable. An explicit settlement ledger with aligned fill rows and held-share payoffs. A longitudinal record of decisions *and* the reasoning behind them — not just a list of trades.
3. **AI partner** — A live read on the game, grounded in the actual feed and your own strategy docs. It pulls real-time score, shots, and book state from the local API, reasons over it, and stages entry/exit ideas as amber cards on the site. You confirm. It cannot place an order — that line is hard.

## How the AI partner actually works

There is **no LLM library anywhere in this codebase** — no API key, no `anthropic` import, no provider SDK. The partner *is* a [Claude Code](https://claude.com/claude-code) terminal session running the `lutz` skill. It reaches the app only through localhost HTTP:

- `GET /partner/context` — one call returns everything it reasons on: live game state, your open positions, recent fills, relevant news.
- `POST /partner/suggestions` — its single write power: stage an amber entry/exit card. The human still confirms.

The context endpoint composes from the *same serializers the dashboard renders from* — so the partner sees byte-for-byte the numbers on your screen. If the site says a position is +52%, the partner sees +52%, because it's the same code path. Single source of truth, end to end.

## Architecture

Monolith. Python 3.12 / FastAPI backend + React 19 / TypeScript dashboard + SQLite. Single process, bound to `127.0.0.1` only.

```
backend/   FastAPI app, Kalshi REST+WS client, SQLAlchemy models, ingestion, partner data plane
dashboard/ Vite 8 + React 19 + Tailwind v4 + TanStack Query
docs/      Brainstorms, plans, strategy notes, the AI partner's context docs
```

Money is integer cents everywhere — never floats. Live orderbooks come over Kalshi's WebSocket (snapshot-then-subscribe, with fractional-delta accumulation done right). The dashboard updates from a single WS connection straight into the TanStack cache. ESPN's free feed drives live scores and news; The Odds API drives cross-book lines.

See `docs/plans/` for the implementation history and `CLAUDE.md` for the design conventions this thing is held to.

## Single-user, local-only by design

The backend binds to `127.0.0.1:8000` with no authentication — OS-level access to the machine *is* the auth. (A separate nginx + HTTP Basic layer is what fronts the author's own deployment; the clone-and-run app needs none of it.)

To run your own, you bring:

- Your own Kalshi API key + RSA private key
- Your own The Odds API and ESPN-feed access
- Your own Claude Code session to be the partner

Nothing in the repo holds personal data. `.env`, `*.pem`, the SQLite database, and `data/` are all gitignored.

## Setup

```bash
git clone https://github.com/Zyori/kalshibot3.git
cd kalshibot3
cp .env.example .env
# fill in .env

./start.sh
```

`start.sh` is a thin wrapper around systemd, which owns process management: single-instance by cgroup (not a fragile PID-file dance), auto-restart on crash, start on boot. Single-instance is non-negotiable — two instances against Kalshi at once double every position. (Learned the expensive way in V1.)

## Status

Shipped and live, trading the 2026 World Cup:

- **Workstation** — live/upcoming board, cross-book odds, one-click limit orders, position cards with realized + unrealized P&L, full chart history.
- **Logbook** — auto-tagged bets, filterable ledger, explicit settlement rows with held-share payoffs.
- **AI partner** — live game read, strategy-grounded suggestions staged as amber cards over localhost.
- **Hardening** — fractional-delta orderbook accuracy, WS book ownership, systemd watchdogs, cross-market isolation (this account also holds non-soccer positions the app must never touch).

NFL is the next sport plugin. Provider-backed (non-terminal) AI is a deliberate *maybe-later* — the terminal-partner model is working.

## License

Personal use. No license granted for redistribution at this time.
