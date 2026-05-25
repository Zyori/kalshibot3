# kalshibot3 — Project Instructions

These are project-specific conventions. They supplement (and where they conflict, override) `~/.claude/CLAUDE.md`.

## What this app is

A personal, single-user Kalshi sports-betting workbook with an AI brain. Three layers (workstation → logbook → AI partner). Soccer first, sport-agnostic underneath. Live trading is human-confirmed — every order is staged then confirmed. No autonomous trading.

See `docs/plans/2026-05-25-001-feat-kalshi-betting-assistant-dashboard-plan.md` for the full plan and `docs/brainstorms/` for the thinking that led there.

## Hard rules

1. **Money is integer cents, everywhere.** Never floats, never `Decimal`. Dollar-to-cents conversion happens at the Kalshi wire boundary in `backend/src/kalshi/schemas.py` and nowhere else. The rest of the codebase trusts that prices in the DB and in app memory are cents (1–99 for Kalshi contracts).
2. **Single source of truth.** Each piece of data has exactly one canonical home. Kalshi is the source of truth for positions and fills — our DB mirrors it. The DB is the source of truth for our ledger metadata (reasoning, tags). The frontend reads, never invents.
3. **No dead code, no duplicate functions, no commented-out blocks.** If a thing was useful, it's in git history. Delete don't comment.
4. **Bind to 127.0.0.1 only.** Localhost is the authentication mechanism. Never `0.0.0.0`. CORS is whitelisted to `http://localhost:5173`, not `*`.
5. **PEM file perms ≤ 0o600 or the app refuses to start.** Checked in `core/auth.py`. This catches the "key accidentally world-readable" mistake.
6. **Client order ID (UUID) on every Kalshi order.** Idempotency. Double-submission is one of the easy ways to lose real money fast.
7. **Demo vs production is an env var.** No UI toggle. A persistent "PRODUCTION" banner shows whenever live money is on the line.
8. **SQLite PRAGMAs on every connection.** `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`, `foreign_keys=ON`. Without `foreign_keys=ON`, every FK in the schema is decorative. Set via SQLAlchemy connect event listener — never trust per-call code to remember.
9. **LLM provider is abstracted.** All LLM calls go through `backend/src/llm/client.py`. No `anthropic.` or `openai.` imports outside that file. We haven't picked a provider yet; this keeps the choice cheap to revisit.
10. **Commit identity is `Zyori <15717413+Zyori@users.noreply.github.com>`.** Not Claude, not co-authored. Repo-local git config is set; verify it's still set before committing.

## Style

- **Simplicity over defensiveness.** Minimum code that solves the problem. No abstractions nobody asked for. Don't add input validation, retries, or fallback logic for situations that can't actually happen.
- **Small, well-named modules.** Prefer many small files over a few that do a lot. If a file is doing two things, it's two files.
- **Surgical changes.** Don't touch code unrelated to the task. Flag unrelated issues (bugs, smells, security) without fixing them unsolicited.
- **Comments are for non-obvious "why," not "what."** Identifier names cover the what. Only comment when the code's intent isn't clear from reading it, when there's a constraint or invariant a future reader couldn't guess, or when something would surprise them.
- **Type everything.** Python: full type hints, `mypy --strict` clean where practical. TypeScript: no `any` without a comment justifying it.

## File map (target structure)

```
backend/
  src/
    main.py              FastAPI app + lifespan
    config.py            Pydantic Settings (.env loader)
    core/
      auth.py            Kalshi RSA-PSS signer, PEM perm check
      db.py              SQLAlchemy engine + session + PRAGMA listener
      ws_manager.py      Browser-WS broadcaster, 500ms coalescing
      types.py           StrEnums, branded types (Cents, Contracts)
      exceptions.py      BotError taxonomy
      logging.py         Structured key=value logging
    models/              SQLAlchemy models, one per file
    kalshi/
      rest.py            REST client + token bucket rate limiter
      ws.py              WS client, snapshot-then-subscribe reconnect
      schemas.py         Wire format (Pydantic). Cents conversion ONLY here.
      order_manager.py   Order lifecycle, emits typed events
    services/
      bet_service.py     BET persistence, auto-tagging, listens to OrderFilled
      risk_manager.py    Hard limits (per-order, daily loss, kill switch)
    ingestion/
      api_football.py    Live scores/events poller
      odds_api.py        Cross-book odds poller
    sports/
      base.py            Sport protocol
      soccer.py          Soccer-specific events, markets, strategy knowledge
    strategy/
      kelly.py           Kelly sizing
      analyzer.py        LLM analysis at decision points
      suggester.py       Proactive suggestion engine
    llm/
      client.py          Provider-agnostic LLM adapter
    api/
      routes/            sports, bets, chat, ledger, health
      ws.py              Browser WS endpoint
    supervisor.py        Background task orchestration

dashboard/
  src/
    main.tsx
    App.tsx              Router + layout shell
    pages/               Dashboard, SportPortal, Ledger, Settings
    components/
      ui/                Primitives: Card, Badge, Button, Skeleton...
      trading/           OrderPanel, PositionCard, MarketRow, KellyDisplay
      chat/              ChatPanel, ChatMessage, SuggestionCard
      charts/            PnLChart, StrategyBreakdown
    contexts/
      WebSocketProvider.tsx  React context. setQueryData on WS events.
    hooks/               usePositions, useMarkets, useLedger, useChat
    lib/
      api.ts             Typed fetch wrapper
      format.ts          Price/odds/date formatters
      sounds.ts          Audio utility (queue + mute + rate limit)
    styles/
      theme.css          Tailwind v4 @theme block, dark palette
```

## Reference repos (read-only)

- `/var/www/_reference/Kalshi-Bot/` — V1, TypeScript. Source of: full REST order management, WS wire format, dashboard CSS palette, `start.sh` single-instance pattern.
- `/var/www/_reference/Kalshi-Mean-Reversion-Bot/` — V2, Python. Source of: RSA-PSS auth, token bucket rate limiter, Kelly sizing, supervisor pattern, SQLAlchemy patterns.

When porting from either, note the source in the commit message ("port: ... from V1/V2").

## Frontend specifics

- Tailwind v4 — CSS-first `@theme` in `theme.css`. No `tailwind.config.ts`.
- Three semantic colors only: green (gain), red (loss), amber (action/proposed). Suggestions are amber, never red.
- All monetary values use `font-mono tabular-nums`.
- Skeleton loaders, not spinners — maintain spatial layout.
- WebSocket = React context provider, not a hook. Single connection. WS events update TanStack cache via `setQueryData`, never `invalidateQueries`.
- No Zustand. TanStack Query + `useState`/`useReducer` is enough.

## What we explicitly do NOT have

- User authentication (localhost binding is the auth)
- Mobile responsive layouts (one viewport: your monitor)
- Multi-user support
- An automated trading mode (human confirms every order — no exceptions)

## When in doubt

Read the plan. Then ask one focused question before guessing.
