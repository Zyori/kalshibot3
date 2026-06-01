---
title: "feat: AI Partner — terminal-cockpit skill + site actuator (Phase 4)"
type: feat
status: active
date: 2026-05-31
deepened: 2026-05-31
origin: docs/plans/2026-05-25-001-feat-kalshi-betting-assistant-dashboard-plan.md
---

# AI Partner — Terminal-Cockpit Skill + Site Actuator (Phase 4)

> **Deepened 2026-05-31** — verified every reuse claim against the live code.
> Material corrections folded in: U8 (auto-tag) cut to follow-up (the metadata
> PATCH can't express an AI draft without a backend change); R7/U4/U6/U7 now
> use `invalidateQueries` (the existing discrete-event pattern — the original
> "never invalidateQueries" was false for discrete events); U6 grows to add an
> OrderPanel pre-fill mechanism (none exists) and renders exit cards inside the
> expanded `MarketCard` (the `PositionCard` component is not on the live trading
> surface); U7 grows to add the ESPN-change observer the supervisor lacks; U3
> reclassified as a user-authored input (the user writes the persona voice).
> Strategy docs reframed: `soccer.md` → `global-principles.md` +
> `soccer-principles.md`, which softens "never hold to settlement" into
> "exit-biased EXCEPT when the game state strongly favors you." See the
> **Plan-vs-Code Corrections** block under Open Questions.

## Summary

Build the Layer-3 "AI partner" without any programmatic LLM. The partner is a **Claude Code terminal session** driven by a private skill (`.claude/skills/lutz-partner/`) that pulls live context from the running localhost API, grounds itself in the user's strategy docs, reasons as a master-bettor persona, and writes **entry and exit suggestions** back to the app via new endpoints. The website becomes the **readout and actuator**: suggestions surface as amber cards (entry cards in the feed, exit cards inside the expanded `MarketCard` with a "Stage Sell" button), routed through the existing confirm-then-place order path. The site also fires **dumb, LLM-free threshold nudges** ("worth a look — ask the partner?") at the strategy doc's trigger moments.

The persona/voice (U3) is **authored by the user**, the same way the strategy docs are — the skill grounds in it but does not generate it. Auto-tag ledger drafting is **deferred to follow-up** (see Scope Boundaries): the existing metadata PATCH can't express "AI-drafted, awaiting human review" without a backend change, which is out of scope this phase.

---

## Problem Frame

Phases 1–2 shipped the workstation and logbook. The original plan's Layer 3 assumed the Anthropic API: `supervisor.py` ticks → `analyzer.py` calls an LLM on a timer → autonomous suggestions appear (see origin, Phase 4). The user has rejected the API on cost grounds and wants to use their Claude subscription instead. The subscription is not programmatically callable in a ToS-clean way, which kills every autonomous/timer-driven LLM path in the original design. The user's actual working pattern — already validated in soft-testing — is to *drive a Claude conversation themselves* by pasting screenshots of the app. The pain to remove is that manual context-feeding: the partner should see the same data the site shows, without screenshots, and its advice should be one human confirmation away from an order.

---

## Requirements

- R1. The AI partner runs as a Claude Code terminal session, with **zero** programmatic LLM calls anywhere in backend or frontend code. No Anthropic/OpenAI SDK import is introduced (CLAUDE.md rule 9 stays satisfied: `llm/client.py` remains the only sanctioned home, and it stays empty/unused this phase).
- R2. A private skill `.claude/skills/lutz-partner/SKILL.md` teaches the session to (a) pull live context from the localhost HTTP API, (b) read `docs/ai-context/global-principles.md` + `docs/ai-context/soccer-principles.md` + `docs/ai-context/strategy-glossary.md`, (c) adopt the **user-authored** master-bettor persona, (d) write entry/exit suggestions via documented endpoints. (Auto-tag/ledger drafting is deferred — see Scope Boundaries.)
- R3. A single "context package" read endpoint returns, in one call, the data the partner needs to reason about a game or the whole book: live run-of-play stats, open positions (with unrealized PnL/%), recent trades, and bankroll.
- R4. A "write suggestion" endpoint lets the terminal session create SUGGESTION rows of kind `entry` or `exit`.
- R5. The SUGGESTION model gains an `entry|exit` kind. Exit suggestions reference the market of an open position and carry a sell rationale.
- R6. Entry suggestions surface as amber cards in the sport portal feed; clicking "Stage This Bet" pre-fills the OrderPanel (buy). Exit suggestions surface as amber cards **inside the expanded `MarketCard`** for the held market (the live trading surface where the OrderPanel actually lives); clicking "Stage Sell" pre-fills the OrderPanel (sell, on the held side), routed through the existing `POST /orders/place` confirm-then-place path. The human confirms every order — no autonomous placement (CLAUDE.md: no automated trading). *(Deepening: the `OrderPanel` has no pre-fill API today — it owns its own `side/count/price` state and takes only `{ticker, book}`. R6 therefore includes adding a pre-fill mechanism to `OrderPanel`. The standalone `PositionCard.tsx` component is NOT on the EventView trading surface — position data renders inside `MarketCard` from the `/events` payload — so exit cards attach there, not to `PositionCard`.)*
- R7. New suggestions and nudges reach the browser over the existing WebSocket app-event path (`broadcast_app_event` → `WebSocketProvider` switch). They are **discrete, low-frequency events**, so — like the existing `position_synced`, `fill`, and `market_lifecycle` cases — they trigger a targeted **`invalidateQueries`** on the affected cache key, NOT `setQueryData`. `setQueryData` is reserved for hot orderbook ticks (`orderbook_snapshot`/`orderbook_delta`); forcing discrete events through it would diverge from the codebase and add needless cache-shape code. *(Deepening correction: the original R7 said "never invalidateQueries" — that is false for discrete events; only hot book data uses `setQueryData`.)*
- R8. The site fires LLM-free threshold **nudges** — passive amber "ask the partner?" chips — when: an open position's unrealized return crosses **≥ +50%**, the game clock crosses **75'**, or a **red card** event fires. A nudge is a reminder to open the terminal, not advice, and never stages or places anything.
- R9. *(Cut — moved to Deferred Follow-Up.)* Auto-tag ledger drafting is deferred: `PATCH /ledger/{bet_id}/metadata` accepts only `strategy/source/timing/confidence/tags/human_reasoning` and has no `ai_reasoning` field and no "drafted-by-AI / awaiting-review" marker (both AI and human edits set the same `metadata_edited_at`). Expressing an unreconciled AI draft needs a backend change, which is out of scope this phase.
- R10. All money stays integer cents; suggestions and nudges use the amber semantic color only; cross-market isolation holds (soccer-only, never touches non-soccer positions).

**Origin reference:** Phase 4 of `docs/plans/2026-05-25-001-feat-kalshi-betting-assistant-dashboard-plan.md` (the AI Partner layer), re-architected for the no-API constraint.

---

## Scope Boundaries

- **No programmatic LLM, no autonomous suggestions.** There is no timer, supervisor tick, or background job that calls an LLM to generate suggestions. `strategy/analyzer.py` and `strategy/suggester.py` from the origin plan are **not** built. The partner only acts when the user is in a terminal session.
- **No site chat box.** Powering an in-page chat surface would require the API (rejected) or putting an LLM on the order-server's HTTP trigger (security hole). The browser is readout + actuator only.
- **No memory/context-management subsystem.** Each terminal session *is* the context window; the skill re-pulls fresh state on every call, so a new session is never cold. "Clear context" = `/clear` or a new session. Explicitly NOT building any conversation persistence, summarization, or auto-clear machinery. (The dormant `chat_message` table stays dormant — not used this phase.)
- **No MCP server.** The skill calls the existing HTTP API via the session's shell; no MCP transport is introduced.
- **No new run-of-play data sources.** We reason on what `ingestion/espn_scoreboard.py` already provides. We do NOT add xG, big-chances, saves, posts, or a penalty-event feed. See the Data-Fidelity Boundary note.
- **No external-book dependency for the core read.** Per `global-principles.md`, "cheap/rich" is judged vs. run-of-play by conventional game logic, NOT vs. an external sharp book. The Odds API is not wired into the partner this phase.

### Deferred to Follow-Up Work

- **Auto-tag ledger drafting (was U8/R9):** the partner drafting `strategy`/`tags`/`ai_reasoning` onto a freshly placed bet. Deferred because `PATCH /ledger/{bet_id}/metadata` has no `ai_reasoning` field and no draft/reconciled marker — both AI and human edits set the same `metadata_edited_at`, so "AI-drafted, awaiting review" is inexpressible without a backend change (add `ai_reasoning` to `MetadataPatch` + a `metadata_source` or `ai_draft_pending` flag + a Ledger UI badge). Real work, out of scope this phase. The partner can still *discuss* tags with the user in the terminal; the user tags by hand as today.
- **Parlay / multi-leg suggestions** (`suggestion_group_id` is schema-ready): no UI or skill flow this phase.
- **Calibration tracking** (partner's stated probability vs. realized outcome): needs settled-bet volume; defer.
- **Post-mortem on settlement** (partner reviews a closed bet): defer.
- **Suggestion expiry sweeper:** SUGGESTION has `expires_at` + an `EXPIRED` status, but nothing sweeps it (unlike `settlement_sweeper`). If time-sensitive exit cards ever set `expires_at`, either expire them client-side or add a sweeper. Not built this phase.
- **NFL strategy doc + portal**: soccer docs only this phase; the skill reads per-sport docs generically so an `nfl-principles.md` slots in later with no skill change (it would sit on top of the shared `global-principles.md`, same as soccer).
- **News feed (World Cup + general):** a relevant-news surface on the site feed AND in the partner's context (injuries, lineups, suspensions, off-field drama). Deferred deliberately, not just later: it breaks the "no new run-of-play data sources" scope boundary — it needs a new external source (RSS/news API, possibly paid → the recurring-cost question the API rejection raised), an `ingestion/news.py` poller, storage, dedup, and a context-shape change so `/partner/context` carries it. That's a fresh brainstorm/plan, not a unit to bolt on here. Architecture already supports it cleanly when we do it: an ingestion poller feeding the context endpoint, same shape as `espn_scoreboard.py`. (Origin plan also lists news aggregation as deferred — "manually paste into chat for now.")

---

## Context & Research

### Relevant Code and Patterns

- `docs/ai-context/global-principles.md` — user-authored, sport-agnostic trading layer (it's trading not betting; you can always sell; fade fan favorites; trade the overshoot; don't average down). **Do not author strategy content.** Every sport doc inherits this.
- `docs/ai-context/soccer-principles.md` — user-authored soccer layer that sits on top of `global-principles.md` (three core setups; shots-on-target = real threat, possession/corners = trap; the two danger windows; game-state probability rules of thumb; World Cup 2026 notes). **Do not author strategy content.** *(Replaces the old `soccer.md`. Doctrine shift: the old doc said "never hold to settlement by default"; the new doc keeps the exit bias but carves an explicit exception — "75' is the default close-out UNLESS the game state strongly favors you (e.g. leader up 2 by 75' → hold to resolution)." The persona (U3) and any plan language must reflect exit-biased-EXCEPT-when-ahead, not the old absolute.)*
- `docs/ai-context/strategy-glossary.md` — tag/strategy/source/timing/confidence vocabulary the partner must use verbatim. (Note: `backend/src/api/routes/settings.py` parses *this file specifically* into JSON for the Settings page — the two new principles docs are separate files and don't touch that parser, but the glossary must keep its `## section` / `- **name** — body` shape.)
- `backend/src/models/suggestion.py` — existing SUGGESTION model. Keyed by `market_id` FK (not ticker). Has `side, suggested_price_cents, suggested_size_cents, kelly_fraction_bps, estimated_edge_bps, ai_probability_pct, market_probability_pct, strategy, justification, confidence, urgency, status, rejection_reason, suggestion_group_id, expires_at`. **No `kind` field yet** — U1 adds it.
- `backend/src/api/routes/events.py` — `GET /events/{event_ticker}`: game + child markets + per-side positions + ESPN `EspnEvent`/`TeamStats`/`MatchEvent`. The richest existing context source; the context-package endpoint composes from the same internals.
- `backend/src/api/routes/positions.py` — `GET /positions`: open positions with `unrealized_pnl_cents`, `current_price_cents`, fee-inclusive avg entry. Source for exit-suggestion targeting and the +50% nudge.
- `backend/src/api/routes/ledger.py` — `GET /ledger` (recent trades, filterable) feeds the partner's "recent trades" context. `PATCH /ledger/{bet_id}/metadata` accepts only `strategy/source/timing/confidence/tags/human_reasoning` — **no `ai_reasoning`, no draft marker** — which is why auto-tag drafting (old R9/U8) is deferred, not buildable on existing fields.
- `backend/src/api/routes/orders.py` — `POST /orders/place` already accepts `action: "buy"|"sell"`, has the **ghost-share guard** (refuses selling a side you don't hold) and server-side sanity check, and stamps a `client_order_id` UUID. The "Stage Sell" button reuses this unchanged.
- `backend/src/ingestion/espn_scoreboard.py` — `TeamStats(shots, shots_on_target, possession_pct, corners, yellow_cards, red_cards)`, `MatchEvent(kind: goal|yellow|red|other)`, `EspnEvent(clock/period)`. The nudge trigger source (clock, red card) and the partner's run-of-play backbone.
- `backend/src/core/ws_manager.py` — `broadcast_app_event({"type": ...})`, coalesced fan-out keyed by `type` (repeats within a flush window collapse). Existing app-event payload is `position_synced` (the only one that goes through `broadcast_app_event`; `fill`/`user_order`/`market_lifecycle` are serialized Kalshi WS messages). R7/R8 add `suggestion` and `nudge` as new app-event payloads through this same method.
- `backend/src/supervisor.py` — `_broadcast_position_synced` (the position-sync hook, wired via `position_syncer.set_on_synced`) shows the emit pattern; it currently fires with **no payload**. **There is no "live-state update hook."** ESPN polls on its own loop (`espn_scoreboard.run()`) and nothing in the supervisor observes ESPN transitions. U7 must ADD an ESPN-change observer (a supervisor task that diffs successive ESPN snapshots for clock-cross-75' and new red-card events) — this is new infrastructure, not an existing hook.
- `dashboard/src/contexts/WebSocketProvider.tsx` — `switch (event.type)`. Discrete events (`position_synced`, `fill`, `market_lifecycle`) use **`invalidateQueries`**; only `orderbook_*` use `setQueryData`. Add `case 'suggestion'` and `case 'nudge'` that `invalidateQueries` the relevant key(s) — consistent with the discrete-event precedent.
- `dashboard/src/components/event/MarketCard.tsx` — the live trading surface per child market (top-of-book, depth, **OrderPanel**, position display from the `/events` payload). Exit cards render **here**, inside the expanded MarketCard for the held market — NOT in the standalone `PositionCard.tsx`, which is not mounted on EventView.
- `dashboard/src/components/trading/OrderPanel.tsx` — props are **only `{ticker, book}`**; it owns its own `side/count/price` `useState` and has **no external pre-fill path**. "Stage This Bet"/"Stage Sell" therefore require ADDING a pre-fill mechanism (controlled `initialSide/initialPrice/initialCount` props, or an imperative ref handle). This is new work in U6, not reuse of an existing staging API.

### Institutional Learnings

- **Cross-market isolation** (`feedback_cross_market_isolation`): the partner and every new endpoint must stay soccer-only and never read/modify non-soccer positions. The context-package endpoint inherits the soccer-only filter `position_sync`/`events.py` already apply.
- **No hard risk limits** (`feedback_no_hard_risk_limits`): the partner *advises* sizing; nothing blocks the user. Nudges and suggestions warn, never gate.
- **No external fill reconciliation** (`feedback_no_external_fill_reconciliation`): when auto-tag drafting lands (deferred follow-up), it must apply only to bets placed through lutz.bot, never to externally-placed Kalshi fills.
- **Timezone normalize to Eastern** (`feedback_timezone_normalize_eastern`): any user-facing time the context endpoint emits for display follows the Eastern-at-the-boundary rule; storage stays UTC.

### External References

- None. No new framework or third-party surface; this phase composes existing internal endpoints and adds a markdown skill. (Skill authoring format follows the repo's existing `.claude/skills/` convention if present; otherwise the standard `SKILL.md` frontmatter + body.)

---

## Key Technical Decisions

- **The "perfect system prompt" lives in the skill, not backend.** With no programmatic LLM, the persona/behavioral-rules/grounding instructions are the skill's body. This is the home for origin's "system prompt structure" item. **The persona voice itself (U3) is authored by the user** — like the strategy docs — and is an *input* to U5, not something the implementing agent drafts (the strategy docs carry a specific opinionated register a generic draft would flatten).
- **Suggestion `kind` is a new enum (`entry|exit`), not a reuse of `strategy` or `urgency`.** `strategy=hedge` describes *why*; `kind=exit` describes *what action*. Conflating them would break the existing entry semantics. Exit suggestions still carry a `strategy` (typically `hedge` or the original thesis) and a `justification` (the sell rationale).
- **Exit suggestions reference the position's market via `market_id`.** The skill works in tickers; the write endpoint resolves ticker → `market_id` (soccer-only) before insert. Keeps the model unchanged in shape (still FK-keyed).
- **Auto-tag drafting is deferred, not solved.** The original framing claimed `PATCH /ledger/{bet_id}/metadata` could carry an AI draft. It can't: the PATCH body has no `ai_reasoning` field and no draft/reconciled marker (both AI and human edits set the same `metadata_edited_at`). A proper draft needs a backend change (new field + flag + UI badge), so this moves to Deferred Follow-Up. The BET row stays the single source of truth for tags; the partner discusses tags in the terminal and the user tags by hand this phase.
- **Nudges are pure if-this-then-that, evaluated server-side off existing data, but the trigger plumbing is partly new.** +50% rides the existing position-sync hook (`position_syncer.set_on_synced`). Clock-cross-75' and red-card have **no existing hook** — the supervisor doesn't observe ESPN — so U7 adds an ESPN-snapshot observer task that diffs successive snapshots. The +50% path also needs a position→event→clock derivation (`Position.kalshi_ticker` → `event_ticker` via the market-discovery feed → ESPN clock); the linkage exists but isn't pre-wired. No new *external* poller (ESPN already polls); the observer reads the existing snapshot. Nudges are **edge-triggered** (fire once per crossing, not every tick) to avoid spam — the evaluator tracks "already nudged for this (subject, trigger)" in memory, reset when the position closes or the game ends. *In-memory is deliberate: a nudge is a reminder to look, not money or an action; the worst case after a mid-game backend restart is one redundant amber chip. Persisting it would add a table + write path + cleanup for zero money-safety benefit — a defensiveness the project rules say to skip.*
- **The context-package endpoint is read-only and composes existing query logic** rather than re-querying raw tables, preserving single-source-of-truth (the partner sees exactly what `events.py`/`positions.py`/`ledger.py` would return). Bankroll has **one** source: `app.state.kalshi_balance_cents`, refreshed by `health.refresh_balance()` on a 10s TTL — U2 reads that app-state value (it may be up to ~10s stale; acceptable for a human-in-the-loop read). There is no balance *service* to call.
- **WS delivery uses `invalidateQueries`, matching the discrete-event precedent** (`position_synced`/`fill`/`market_lifecycle`), not `setQueryData` (which is for hot orderbook ticks only). This is both consistent and less code than the original plan's cache-shape approach.
- **Exit-race backstops are three different failure points, not three redundant layers.** (A) Frontend hides exit cards for positions that no longer exist — UX, prevents offering a sell of nothing. (B) Backend write-time guard rejects an exit suggestion for an unheld position — *optional*; it only guards the write moment, and a position can still close after a valid write. (C) `/orders/place` ghost-share guard (`orders.py:157`) checks at **execution time** and is the layer that actually catches the race; it already exists and is unconditional. Keep A + C as load-bearing; treat B as optional (it adds a position query on every suggestion write for a case C already covers).
- **Default `vite build --watch` deployment** means new frontend cards rebuild on save with no dev server (per deployment memory).

---

## Open Questions

### Plan-vs-Code Corrections (deepening pass, 2026-05-31)

Each reuse claim in the original plan was checked against the live code. What changed:

- **U8/R9 auto-tag — CUT.** Claim: "reuse existing fields on `PATCH /ledger/{id}/metadata`." Reality (`ledger.py:399-462`): `MetadataPatch` has no `ai_reasoning` and no draft marker; both AI and human edits set the same `metadata_edited_at`. → Deferred to Follow-Up.
- **R7 WS rule — REWRITTEN.** Claim: "`setQueryData`, never `invalidateQueries`." Reality (`WebSocketProvider.tsx:261-285`): discrete events (`position_synced`/`fill`/`market_lifecycle`) all use `invalidateQueries`; only `orderbook_*` use `setQueryData`. → suggestion/nudge use `invalidateQueries`.
- **Exit-card placement — MOVED.** Claim: "attach to `PositionCard`." Reality: `PositionCard.tsx` is a standalone `/positions`-polling component **not mounted on EventView**; the live trading surface is `MarketCard` (renders the OrderPanel + position from the `/events` payload). → exit cards render inside the expanded `MarketCard`.
- **OrderPanel pre-fill — NEW WORK.** Claim: "pre-fill via existing staging entry points." Reality (`OrderPanel.tsx:51-57`): props are `{ticker, book}` only; it owns its `side/count/price` state with no external pre-fill path. → U6 adds a pre-fill mechanism.
- **Nudge "live-state hook" — DOESN'T EXIST.** Claim: "clock/red-card from the live-state update hook (already in supervisor.py)." Reality: the supervisor has no ESPN observer; ESPN polls on its own loop. → U7 adds an ESPN-snapshot observer + the position→event→clock derivation.
- **Bankroll source — CLARIFIED.** Only source is `app.state.kalshi_balance_cents` (10s TTL via `health.refresh_balance()`); no balance service.
- **`broadcast_app_event` payloads — CLARIFIED.** Today only `position_synced` flows through it; `fill`/`user_order`/`market_lifecycle` are serialized Kalshi WS messages. New `suggestion`/`nudge` are new app-event payloads.

### Resolved During Planning

- **Does the order path support sells?** Yes — `POST /orders/place` takes `action:"sell"` with a ghost-share guard (`orders.py:157`). No new order logic needed for exit; this guard is the load-bearing exit-race backstop.
- **Does an entry suggestion→card→stage flow already exist on the frontend?** **No.** `components/chat/` doesn't exist and nothing renders SUGGESTION rows today. The model exists; the UI does not. Entry-card UI **and an OrderPanel pre-fill mechanism** are new work (U6), not reuse.
- **How do new suggestions reach the browser?** `broadcast_app_event` → new `suggestion`/`nudge` cases in `WebSocketProvider` using `invalidateQueries`. No new transport.
- **Should the persona voice be drafted by the implementing agent?** **No** — the user authors it (U3 is an input, like the strategy docs). U5 wires it.
- **Memory management?** Dissolved — no subsystem (see Scope Boundaries).
- **Strategy docs?** `soccer.md` replaced by `global-principles.md` + `soccer-principles.md`; the new soccer doc softens "never hold to settlement" into exit-biased-EXCEPT-when-the-game-favors-you.

### Deferred to Implementation

- **Exact context-package JSON shape** — finalize field names against the live `events.py`/`positions.py` serializers when implementing U2, so the partner sees identical numbers to the site.
- **Nudge de-dup key granularity** — exact in-memory key (e.g. `(ticker, side, trigger)`) and reset conditions settle when wiring U7 against the real supervisor position-sync hook + the new ESPN observer.
- **OrderPanel pre-fill shape** — controlled props (`initialSide/initialPrice/initialCount`) vs. an imperative ref handle — decide at implementation against how `MarketCard` mounts the panel (U6).
- **Whether the write-suggestion endpoint needs an `expires_at` default for exit cards** (time-sensitive sells) — note there is no expiry sweeper, so any `expires_at` must be honored client-side this phase. Decide against the live UI when building U4/U6.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
            TERMINAL (the cockpit)                         BROWSER (readout + actuator)
  ┌─────────────────────────────────────┐         ┌──────────────────────────────────────┐
  │  Claude Code session                 │         │  lutz.bot dashboard                    │
  │  + skill: .claude/skills/lutz-partner│         │                                        │
  │                                      │         │   SportPortal feed                     │
  │  reads:                              │  HTTP   │     └─ amber ENTRY card [Stage This Bet]│
  │   GET /api/partner/context?event=…  ─┼────────▶│   MarketCard (expanded, held market)   │
  │   (run-of-play + positions +         │  (read) │     └─ amber EXIT card  [Stage Sell]    │
  │    recent trades + bankroll)         │         │        (NOT the standalone PositionCard)│
  │   global-principles + soccer-        │         │   passive nudge chip:                  │
  │   principles + strategy-glossary     │         │     "USA YES +52% — ask the partner?"  │
  │                                      │         │            ▲                           │
  │  persona: master bettor              │         │            │ WS app-event              │
  │  (USER-AUTHORED, in skill)           │         │            │                            │
  │                                      │         │            │ {type:'nudge'|'suggestion'}│
  │  writes:                             │  HTTP   │   ┌────────┴─────────┐                 │
  │   POST /api/partner/suggestions ─────┼────────▶│   │ broadcast_app_   │                 │
  │     {kind:entry|exit, ticker, side,  │ (write) │   │ event →          │                 │
  │      price, size, strategy, just…}   │         │   │ invalidateQueries│                 │
  │                                      │         │   └──────────────────┘                 │
  │  (auto-tag draft: DEFERRED)          │         │                                        │
  └─────────────────────────────────────┘         │   [Stage *] → OrderPanel(pre-filled) →  │
                                                   │   confirm → POST /orders/place         │
   nudges (server-side, NO LLM):                   │   (action buy|sell, client_order_id    │
     position +50% [position-sync, EXISTS] ─┐      │    UUID, ghost-share + sanity guard)   │
     clock>75'  [NEW ESPN observer] ────────├─▶    └──────────────────────────────────────┘
     red card   [NEW ESPN observer] ────────┘  edge-triggered ─▶ broadcast_app_event({type:'nudge'})
```

Suggestion lifecycle (entry and exit share one model, differ by `kind`):

```
partner writes ─▶ SUGGESTION(status=pending, kind=entry|exit, urgency)
                       │  broadcast_app_event({type:'suggestion'}) → invalidateQueries(['suggestions'])
                       ▼
       amber card appears (feed if entry, inside expanded MarketCard if exit)
                       │
        user clicks Stage ─▶ OrderPanel pre-filled (new pre-fill API) ─▶ confirm ─▶ /orders/place
                       │                                              │
              user dismisses                                  order placed (BET row,
                       ▼                                       suggestion_id linked)
            status=rejected                                   status=accepted, acted_on_at set

Note: SUGGESTION has no FK to BET. Linking a placed order back to its suggestion
(suggestion_id on BET, status=accepted/acted_on_at on SUGGESTION) is its own small
wiring step — confirm whether BET carries suggestion_id today when building U6, else
the "linked" arrow is aspirational this phase.
```

---

## Implementation Units

- U1. **Add `kind` to the SUGGESTION model**

**Goal:** Distinguish entry suggestions from exit (sell) suggestions on one model.

**Requirements:** R5

**Dependencies:** None

**Files:**
- Modify: `backend/src/core/types.py` (add `SuggestionKind(StrEnum)` = `ENTRY|EXIT`)
- Modify: `backend/src/models/suggestion.py` (add `kind` column, NOT NULL, CHECK in `('entry','exit')`; default `entry` for the migration backfill of any existing rows)
- Create: `backend/alembic/versions/<rev>_add_suggestion_kind.py`
- Test: `backend/tests/test_models/test_suggestion_kind.py`

**Approach:**
- New StrEnum mirrors the existing enum style in `types.py`.
- Column is NOT NULL with a CHECK constraint mirroring the existing `ck_suggestion_*` pattern. Migration backfills existing rows to `entry` (there are none in practice, but keep the migration safe).
- No change to FK shape — exit suggestions still reference `market_id`.

**Patterns to follow:** existing CHECK constraints and StrEnum columns in `backend/src/models/suggestion.py` and `backend/src/core/types.py`.

**Test scenarios:**
- Happy path: insert a suggestion with `kind='entry'` and one with `kind='exit'`; both persist and round-trip.
- Edge case: omitting `kind` rejects (NOT NULL) — confirm the model requires it.
- Error path: `kind='foo'` violates the CHECK constraint and raises.

**Verification:** Migration applies cleanly on a copy of the dev DB; model inserts of both kinds succeed; invalid kind is rejected.

---

- U2. **Context-package read endpoint**

**Goal:** One call returns everything the partner needs to reason — live run-of-play + open positions + recent trades + bankroll — for a given event or the whole book.

**Requirements:** R3, R10

**Dependencies:** None

**Files:**
- Create: `backend/src/api/routes/partner.py` (`GET /partner/context`, optional `?event={event_ticker}`)
- Modify: `backend/src/main.py` (register the partner router)
- Test: `backend/tests/test_api/test_partner_context.py`

**Approach:**
- Compose from the *same serialization* used by `events.py` (run-of-play: score, clock, period, shots, shots_on_target, possession, corners, cards, recent MatchEvents), `positions.py` (open positions with unrealized PnL/%), and `ledger.py` (last N trades). Bankroll: read `request.app.state.kalshi_balance_cents` (the single source; refreshed by `health.refresh_balance()` on a 10s TTL — may be ~10s stale, fine for a human read). Do not add a balance service or recompute.
- `?event=` scopes run-of-play + the positions/markets on that event; no `event` returns the global book (all open positions + recent trades + bankroll, run-of-play omitted or per-live-game summarized).
- Read-only, soccer-only (inherits the existing filters). Times for display follow Eastern-at-boundary.
- Output shaped for *reading by a model in a terminal* — flat, labeled, no nested ceremony. This is the data half of the "perfect prompt."

**Patterns to follow:** `backend/src/api/routes/events.py` (compose game+markets+positions), `positions.py` (PnL fields), `ledger.py` (`_bet_to_dict`).

**Test scenarios:**
- Happy path (scoped): `?event=` for a live game returns run-of-play backbone + that event's positions + recent trades + bankroll; numbers match what `/events` and `/positions` return for the same inputs.
- Happy path (global): no `event` returns all open positions + recent trades + bankroll.
- Edge case: unknown/non-soccer event ticker is refused (cross-market isolation), mirroring `events.py` behavior.
- Edge case: no open positions / no recent trades → empty arrays, not nulls or errors.
- Integration: a position's `unrealized_pnl` in the context payload equals the value `/positions` reports at the same moment (single source of truth).

**Verification:** Hitting the endpoint while a game is live returns a payload a human can read top-to-bottom and understand the game + book state without opening the site.

---

- U3. **Master-bettor persona — USER-AUTHORED INPUT (not built by the implementing agent)**

**Goal:** The persona/behavioral-rules voice the skill grounds in. **The user writes this**, the same way the user wrote the strategy docs — the implementing agent must NOT draft the voice (the strategy docs carry a specific opinionated register a generic draft would flatten into hedge-everything mush). This is an *input* that gates U5/U9.

**Requirements:** R1, R2

**Dependencies:** None — but U5 and U9 depend on this existing. **`ce-work` does not author this; it waits for the user's `persona.md`.**

**Status:** ⛔ Blocking input owned by the user. Treat like `soccer-principles.md`: hand-authored, then the skill references it.

**Files:**
- Author (by user): `.claude/skills/lutz-partner/persona.md` (or a `SKILL.md` section — see U5)
- Reference (read-only, do not edit): `docs/ai-context/global-principles.md`, `docs/ai-context/soccer-principles.md`, `docs/ai-context/strategy-glossary.md`

**Content the user will encode (guidance for the user, not a spec to generate):**
- Persona = a disciplined master bettor whose job is to *stop bad bets, surface good ones, and above all call exits* — exit-biased, **but with the new doctrine exception**: 75' is the default close-out UNLESS the game state strongly favors you (leader up 2 by 75' → hold to resolution). Frame relative to the user's position (same event, opposite action).
- Threat reading: shots-on-target/cards = real threat; possession/corners = trap; no "overdue" goal; pressure alone is not a thesis-break (require real-chance evidence); re-rate after one soft goal.
- The **data-fidelity boundary** as an explicit instruction: "You see shots-on-target, cards, score, clock — NOT xG, saves, posts, big-chances, or explicit penalties. Ask the user for broadcast texture when it would change the read; never fabricate threat signals you can't see."
- Ground every read in the strategy docs read at session start, not from memory.

**Patterns to follow:** the tone of `global-principles.md` + `soccer-principles.md` (opinionated, judgment-not-rules).

**Test scenarios:** *None — markdown persona content, no executable behavior. Validated by U9 dry-run.*

**Verification:** A reviewer reading the persona can predict the partner's behavior on the documented setups (early underdog goal → ride draw; leader at 78' up 1 → bank; leader up 2 by 75' → hold), and the data-boundary caveat is unmissable.

---

- U4. **Write-suggestion endpoint (entry + exit)**

**Goal:** Let the terminal session create `entry` or `exit` SUGGESTION rows; broadcast them to the browser.

**Requirements:** R4, R5, R7, R10

**Dependencies:** U1 (kind), U2 (router exists)

**Files:**
- Modify: `backend/src/api/routes/partner.py` (`POST /partner/suggestions`)
- Modify: `backend/src/core/ws_manager.py` ONLY if a helper is wanted — note `broadcast_app_event` already takes a final wire dict, so the route can call it directly with `{"type": "suggestion", ...}` (no new "builder" needed; `_serialize` is bypassed for app events).
- Test: `backend/tests/test_api/test_partner_suggestions.py`

**Approach:**
- Request body: `kind`, `ticker`, `side`, `suggested_price_cents`, `suggested_size_cents`, `strategy`, `justification`, `confidence`, optional `urgency`, optional probability/edge fields, optional `expires_at`. Pydantic-validated: `price 1–99`, `size ≥ 0`, `side ∈ {yes,no}`, `strategy`/`confidence`/`urgency` ∈ their enums, `kind ∈ {entry,exit}`.
- Resolve `ticker → market_id` soccer-only; reject unknown/non-soccer tickers (cross-market isolation).
- For `kind=exit`: **optionally** validate the user holds the referenced (ticker, side) position (deepening: this is backstop B — only guards the write moment; the load-bearing race guard is `/orders/place`'s ghost-share check at execution time. Include it as a cheap sanity check, but it is not what makes the exit safe). A position can still close after a valid write — the frontend (U6) hides stale exit cards and `/orders/place` refuses the sell.
- Insert `status=pending`; emit `broadcast_app_event({"type": "suggestion", "suggestion_id": <id>, ...})`. The frontend `case 'suggestion'` does `invalidateQueries(['suggestions'])` (discrete-event pattern, not `setQueryData`).
- No LLM, no autonomous behavior — this is a thin authenticated-by-localhost write the session calls.

**Patterns to follow:** `backend/src/api/routes/orders.py` (Pydantic body + guard pattern), `supervisor._broadcast_position_synced` (calls `broadcast.broadcast_app_event({"type": ...})` directly — copy that, not a `_serialize` builder).

**Test scenarios:**
- Happy path (entry): valid entry body inserts a pending entry suggestion and emits one `suggestion` app event.
- Happy path (exit): valid exit body, on a held position, inserts a pending exit suggestion + emits event.
- Edge case: `kind=exit` for a (ticker, side) not held → rejected (optional backstop B) — keep if implemented.
- Error path: non-soccer ticker → rejected (cross-market isolation).
- Error path: `price=0`/`price=100`/bad side/bad strategy enum → 422.
- Integration: after a successful POST, a subscribed WS client receives a `suggestion` app event carrying the new row's id.

**Verification:** From a terminal `curl`, creating an entry and an exit suggestion both succeed and a connected browser receives the broadcast.

---

- U5. **The skill itself — `SKILL.md` (wiring + workflow)**

**Goal:** The private skill that makes a Claude Code session into the partner: how to pull context (U2), how to ground (U3 persona + docs), how to write back (U4), and the working loop. (Auto-tag/U8 is deferred — do NOT document a `PATCH /ledger/{id}/metadata` draft recipe; that path can't express an AI draft this phase.)

**Requirements:** R1, R2

**Dependencies:** U2, U4 (the endpoints it documents); **U3 (user-authored persona) must exist** before this is finalized.

**Files:**
- Create: `.claude/skills/lutz-partner/SKILL.md` (frontmatter: name, description; body: the context-pull recipe, the write recipes, the working loop; references the user-authored `persona.md`)
- Reference (user-authored, do NOT generate): `.claude/skills/lutz-partner/persona.md` (from U3)

**Approach:**
- Document the exact localhost calls: `GET /partner/context[?event=]` to load state; `POST /partner/suggestions` to advise. Use the base URL the app serves on (127.0.0.1 bind — confirm the port from `config.py`/the systemd unit when writing this).
- Ground in `docs/ai-context/global-principles.md` + `docs/ai-context/soccer-principles.md` + `docs/ai-context/strategy-glossary.md`, read at session start.
- Working loop the skill prescribes: (1) on each user question, re-pull `/partner/context` for fresh state — never reason from stale memory; (2) read the strategy docs if not already in this session; (3) reason as the persona; (4) when recommending action, write a suggestion so it lands as a card; (5) tell the user it's staged on the site for confirm.
- State the no-autonomy contract plainly: the partner suggests; the human confirms every order on the site.
- Re-pull-every-call is what makes "memory management" a non-issue (per Scope Boundaries) — document that explicitly so a future reader doesn't add a memory system.

**Patterns to follow:** existing `.claude/skills/` entries if any; otherwise standard SKILL.md frontmatter + body.

**Test scenarios:** *Test expectation: none — skill is markdown instructions. Validated end-to-end by U9.*

**Verification:** A fresh Claude Code session, told to use the skill, pulls context and produces a grounded read + a staged suggestion without the user pasting any data (U9 proves this live).

---

- U6. **Frontend: entry + exit amber suggestion cards**

**Goal:** Render SUGGESTION rows as amber cards — entry cards in the sport portal feed, exit cards inside the expanded `MarketCard` for the held market — each with a Stage button that pre-fills the OrderPanel. **Includes adding a pre-fill mechanism to `OrderPanel` (none exists today).**

**Requirements:** R6, R7, R10

**Dependencies:** U1 (kind), U4 (broadcast + endpoint)

**Files:**
- Create: `dashboard/src/components/trading/SuggestionCard.tsx` (amber; renders side, price, size, strategy, confidence, justification; "Stage This Bet" for entry / "Stage Sell" for exit; "Dismiss")
- **Modify: `dashboard/src/components/trading/OrderPanel.tsx` — add a pre-fill mechanism.** Today its props are `{ticker, book}` and it owns `side/count/price` `useState` with no external entry point. Add controlled `initialSide/initialPrice/initialCount` props (or an imperative ref handle) and a one-shot "apply pre-fill" effect that sets state without breaking the existing price-hold/auto-follow logic (`PRICE_HOLD_MS`). This is the load-bearing new work in U6.
- **Modify: `dashboard/src/components/event/MarketCard.tsx` — slot exit cards** for this market's held side, above/within the OrderPanel, and wire a "Stage Sell" click to the new pre-fill API.
- Modify: `dashboard/src/contexts/WebSocketProvider.tsx` (add `case 'suggestion'` → **`invalidateQueries(['suggestions'])`**, matching the discrete-event pattern — NOT `setQueryData`).
- Modify: `dashboard/src/pages/SportPortal.tsx` (render entry suggestion cards in the feed — replaces the current "Suggested Bets" placeholder tile).
- Create: a `useSuggestions` hook + `api.ts` typed fetch for cold-load of pending suggestions (`GET /partner/suggestions?status=pending` — add this read to `partner.py` in U4 or here, decide at impl).
- Test: `dashboard/src/components/trading/SuggestionCard.test.tsx`

**Approach:**
- Cold-load pending suggestions via TanStack Query (`['suggestions']`); the WS `suggestion` case `invalidateQueries(['suggestions'])` so the next fetch reads the new row. Discrete event → invalidate, consistent with `position_synced`.
- Entry "Stage This Bet" → OrderPanel pre-filled as **buy** (ticker, side, price, size). Exit "Stage Sell" → OrderPanel pre-filled as **sell** on the **held side** (so the ghost-share guard passes). User still reviews + confirms.
- Exit cards render inside the expanded `MarketCard` for the held market; entry cards render in the SportPortal feed. (The standalone `PositionCard.tsx` is not on the EventView surface — do not attach there.)
- Stale exit card: if the targeted position no longer appears in `/positions` (closed between write and click), hide the card — backstop A. `/orders/place` is the final guard if the user clicks anyway.
- Amber only — never green/red — per the three-color rule. Money in `font-mono tabular-nums`.
- Dismiss → mark suggestion `rejected` (small write; PATCH on the suggestion or a dismiss endpoint — decide at impl, keep minimal).

**Patterns to follow:** existing discrete-event `invalidateQueries` cases in `WebSocketProvider.tsx` (`position_synced`); `MarketCard.tsx` layout (where the OrderPanel mounts); the existing entry order-place flow for the post-stage UX.

**Test scenarios:**
- Happy path (entry): a pending entry suggestion renders an amber card in the feed; "Stage This Bet" pre-fills OrderPanel as buy with matching side/price/size.
- Happy path (exit): a pending exit suggestion on a held position renders inside that market's expanded MarketCard; "Stage Sell" pre-fills OrderPanel as sell on the held side.
- Pre-fill mechanism: applying a pre-fill sets side/price/count and does not get immediately stomped by the OrderPanel's auto-follow (`PRICE_HOLD_MS`) — the pre-filled price holds long enough to confirm.
- Edge case: exit suggestion whose position no longer exists (closed) → card does not render (backstop A), never offers a sell of nothing.
- Edge case: dismiss removes the card and marks the row rejected; it does not reappear on reload.
- Integration: a `suggestion` WS event triggers `invalidateQueries(['suggestions'])` and the refetched list renders the card.

**Verification:** With the backend running, a suggestion POSTed from a terminal appears as the correct amber card within the WS coalescing window and stages the correct order with the right side/price/count pre-filled.

---

- U7. **Threshold nudges (server-side, LLM-free)**

**Goal:** Fire passive "ask the partner?" nudges at the strategy doc's trigger moments, edge-triggered, with no LLM and no autonomous action.

**Requirements:** R8, R7, R10

**Dependencies:** rides the existing position-sync hook + WS; **adds a new ESPN-snapshot observer to the supervisor** (the clock/red-card "live-state hook" does NOT exist today).

**Files:**
- Create: `backend/src/services/nudge_evaluator.py` (pure if-this-then-that; in-memory edge-trigger de-dup)
- Modify: `backend/src/supervisor.py` — two integration points:
  1. **+50% trigger:** call the evaluator from the existing position-sync `set_on_synced` hook. Needs the position→event→clock derivation: `Position.kalshi_ticker` → `event_ticker` via `market_discovery.get_feed()` → ESPN clock from `espn_scoreboard.snapshot`. (The position-sync callback fires with no payload today; the evaluator re-reads positions + feed + ESPN snapshot itself.)
  2. **Clock-cross-75' + red-card triggers:** add a **new supervisor task** that observes `espn_scoreboard.snapshot` (diff successive snapshots for clock crossing 75' and for newly-appeared red-card `MatchEvent`s). ESPN already polls on its own loop; this observer reads the snapshot on a short interval — no new *external* poll. Emit `broadcast_app_event({"type": "nudge", ...})`.
- Modify: `dashboard/src/contexts/WebSocketProvider.tsx` (add `case 'nudge'` → `invalidateQueries(['nudges'])`, or hold nudges in a small client cache the chip reads — decide at impl; discrete-event pattern either way, not `setQueryData` of hot book data).
- Create: `dashboard/src/components/trading/NudgeChip.tsx` (passive amber chip: "USA YES +52% — ask the partner?")
- Modify: `dashboard/src/pages/SportPortal.tsx` and/or `MarketCard.tsx` (render the chip near the relevant position/game)
- Test: `backend/tests/test_services/test_nudge_evaluator.py`

**Approach:**
- Three triggers: position unrealized return **≥ +50%** (from the position-sync hook), game clock crosses **75'**, **red card** `MatchEvent` (both from the new ESPN observer). No new external poller — ESPN already polls; the observer reads its snapshot.
- **Edge-triggered:** fire once per (subject, trigger) crossing. Track fired keys in memory; reset a key when the position closes or the game finishes. A nudge that re-fires every sync would be spam — explicitly avoid. **In-memory is deliberate** (a reminder, not money/an action; worst case after a mid-game restart is one redundant chip — persisting would add a table + write path + cleanup for zero safety benefit, a defensiveness the project rules say to skip).
- A nudge carries only enough to render the chip + identify the subject. It is **not** advice and never stages/places anything (it can't — there's no LLM behind it).
- Cross-market isolation: only soccer positions/games (inherited — positions are already soccer-only; ESPN is soccer-only).
- The evaluator must swallow missing-data cases (pre-match None clock, unsynced PnL) without breaking the sync loop / observer task it hooks into.

**Patterns to follow:** `supervisor._broadcast_position_synced` (calls `broadcast.broadcast_app_event` directly); `espn_scoreboard.run()` (the polling-task shape to mirror for the observer task); the supervisor's `_tasks` list + `start()`/`stop()` lifecycle for adding the new task cleanly.

**Test scenarios:**
- Happy path: position crosses from +48% to +52% → one nudge emitted; staying at +55% on the next sync → no second nudge (edge-trigger).
- Happy path: clock goes 74'→76' → one "75'" nudge; red card event → one "red card" nudge.
- Edge case: position closes then a new one opens on the same ticker → de-dup key reset, nudge can fire again.
- Edge case: a position already above +50% when first seen (app start mid-game) → fires once, then suppressed.
- Error path: missing/None clock or PnL (pre-match, unsynced) → no nudge, no crash.
- Integration: an emitted nudge reaches a subscribed WS client as a `nudge` event and renders a chip.

**Verification:** During a live game (or a simulated live-state feed), nudges appear exactly once per crossing and never spam on repeated syncs.

---

- U8. **Auto-tag ledger drafting — ⛔ CUT (deferred to follow-up)**

**Why cut (deepening 2026-05-31):** The original goal — partner drafts `strategy`/`tags`/`ai_reasoning` onto a freshly placed bet, marked "awaiting human review" — is **not buildable on existing fields**. Verified against `backend/src/api/routes/ledger.py:399-462`: `MetadataPatch` accepts only `strategy/source/timing/confidence/tags/human_reasoning`. There is **no `ai_reasoning` field** in the PATCH body, and **no marker** distinguishing an AI draft from a human edit — both just set `metadata_edited_at`. The plan's "reuse existing fields, prefer NOT modifying" was false; this needs a real backend change (add `ai_reasoning` to `MetadataPatch` + a `metadata_source`/`ai_draft_pending` flag + a Ledger UI badge), which is out of scope this phase.

**Disposition:** Moved to **Deferred to Follow-Up Work** (see Scope Boundaries). The partner can still discuss tags with the user in the terminal; the user tags by hand via the existing Ledger edit UI, as today. U5 must NOT document a draft recipe.

---

- U9. **End-to-end dry run + skill polish**

**Goal:** Prove the whole loop with a live (or simulated-live) game and tune the skill from what actually happens.

**Requirements:** R1–R8 (integration; R9/U8 cut)

**Dependencies:** U1, U2, U4, U5, U6, U7. **Also gated on the user-authored U3 `persona.md` existing.** (U8 cut.)

**Files:**
- Modify: `.claude/skills/lutz-partner/SKILL.md` (tune wiring/recipes from observed behavior). The user may also tune their `persona.md`.
- Create: `docs/ai-context/partner-playbook.md` *(optional)* — a short "how to use the partner" note for the user (when to open a session, what to ask)

**Approach:**
- In a terminal session with the skill: ask for a read on a live game; confirm the partner pulls context (no pasting), grounds in `global-principles.md` + `soccer-principles.md`, gives an exit-aware read (including the hold-when-ahead exception), and stages a suggestion that appears as the right card. Place a tiny test order through the staged suggestion (demo env).
- Confirm nudges fire once per crossing during the session (both the +50% path and the new ESPN-observer clock/red-card paths).
- Tune persona wording where the partner drifts from the strategy docs (user owns the persona edits).

**Execution note:** Manual verification of the integrated system, not an automated test unit. Run against the **demo** environment, not production (real money).

**Test scenarios:** *Covered by manual dry-run; automated coverage lives in U1/U2/U4/U6/U7.*

**Verification:** The user can run a real session: open terminal → ask → see grounded advice → stage → confirm on site — with zero screenshot-pasting.

---

## System-Wide Impact

- **Interaction graph:** New `partner` router (read + write). New WS app-event types `suggestion` and `nudge` flow through the existing `broadcast_app_event` → `WebSocketProvider` switch (consumed via `invalidateQueries`, the discrete-event pattern). Nudge evaluator hooks the existing supervisor position-sync hook AND a **new ESPN-snapshot observer task** (the clock/red-card live-state hook does not exist today and is added by U7). Stage buttons feed the existing OrderPanel (extended with a pre-fill mechanism in U6) → `POST /orders/place`.
- **Error propagation:** Partner endpoints are localhost-only thin writes; bad input → 422, never a crash. Nudge evaluator must swallow missing-data cases (pre-match None clock, unsynced PnL) without breaking the position-sync hook or the ESPN observer task it runs from.
- **State lifecycle risks:** Nudge de-dup is in-memory and edge-triggered — must reset on position close / game end or it leaks keys (steady-state concern per CLAUDE.md). Exit-suggestion validity is time-sensitive (a position can close between suggestion and click) — the card must not offer a sell of a position that's gone.
- **API surface parity:** The exit path deliberately reuses `POST /orders/place` (with its ghost-share guard + sanity check + `client_order_id`), so sells get the same safety as buys. No parallel order path.
- **Integration coverage:** WS broadcast of suggestion/nudge → frontend card render (U4/U6/U7 integration scenarios). Context-package numbers matching `/positions`/`/events` (U2) — single source of truth.
- **Unchanged invariants:** `POST /orders/place` order logic, the sanity guard, `client_order_id` UUID stamping, cross-market isolation, integer-cents money, and the no-autonomous-trading rule are all unchanged — this phase adds a *suggester surface*, never an *executor*. The dormant `chat_message` table stays unused. `llm/client.py` stays empty (no provider chosen, none needed).

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Someone later "improves" this by adding an autonomous LLM tick (re-introducing the API the user rejected) | Scope Boundaries + SKILL.md state the no-autonomy contract explicitly; `llm/client.py` stays empty as a visible signal. |
| Nudges spam on every 60s sync | Edge-triggered de-dup (U7), tested for the "stays above threshold" case. |
| Exit suggestion races a position close → "Stage Sell" on a position you no longer hold | Three distinct points, not redundant layers: (A) frontend hides exit cards for missing positions (U6, UX); (B) optional backend write-time guard (U4, guards only the write moment); (C) `/orders/place` ghost-share guard at execution time (`orders.py:157`) is the **load-bearing** backstop and already exists. A + C are required; B is optional. |
| Partner reasons on trap signals (possession/corners) it shouldn't | User-authored persona (U3) encodes shots-on-target = real threat, possession/corners = trap, from `soccer-principles.md`; data-fidelity boundary stated. |
| Context-package drifts from what the site shows | U2 composes from existing serializers and is tested for equality with `/positions` (single source of truth). Bankroll from the single `app.state.kalshi_balance_cents` source. |
| Persona inherits the stale "never hold to settlement" absolute | The doctrine shifted in `soccer-principles.md` (hold when up 2 by 75'); the plan flags this explicitly and U3 is user-authored against the new docs, U9 verifies the hold-when-ahead behavior. |
| New ESPN observer or position→clock derivation breaks the sync loop | Observer is a separate supervisor task mirroring `espn_scoreboard.run()`; evaluator swallows missing-data; failures log and the loop survives (matches existing `_tick` patterns). |
| Skill calls a wrong/old endpoint shape | U9 dry-run validates live; SKILL.md documents exact current shapes and the serving port. |

---

## Documentation / Operational Notes

- The skill (`.claude/skills/lutz-partner/`) and optional `docs/ai-context/partner-playbook.md` are the user-facing docs for how to run the partner.
- No new env vars, no new API key. Backend already binds 127.0.0.1; the partner uses that. **One new background service** (the U7 ESPN-snapshot observer task) joins the supervisor's `_tasks`; no new external poller.
- Deployment: `vite build --watch` rebuilds the new frontend cards on save; backend restart via the existing systemd unit picks up the new router + the new supervisor task.
- Run U9 against the **demo** environment — exit suggestions place real sell orders in production.

---

## Sources & References

- **Origin plan:** `docs/plans/2026-05-25-001-feat-kalshi-betting-assistant-dashboard-plan.md` (Phase 4, AI Partner — re-architected for no-API).
- **Strategy docs (read-only inputs):** `docs/ai-context/global-principles.md`, `docs/ai-context/soccer-principles.md`, `docs/ai-context/strategy-glossary.md`. *(Replaced the old single `soccer.md` — global layer inherited by all sports + a soccer layer on top.)*
- **User-authored inputs (gating):** `.claude/skills/lutz-partner/persona.md` (U3, written by the user).
- **Key existing code (verified during deepening):** `backend/src/api/routes/{events,positions,ledger,orders,health,settings}.py`, `backend/src/models/suggestion.py`, `backend/src/core/{types,ws_manager}.py`, `backend/src/supervisor.py`, `backend/src/services/position_sync.py`, `backend/src/ingestion/espn_scoreboard.py`, `dashboard/src/contexts/WebSocketProvider.tsx`, `dashboard/src/pages/{SportPortal,EventView}.tsx`, `dashboard/src/components/event/MarketCard.tsx`, `dashboard/src/components/trading/{PositionCard,OrderPanel}.tsx`.
