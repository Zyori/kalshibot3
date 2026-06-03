---
title: "Trade Snapshots for Exit Post-Mortems"
type: feature-spec
status: ready-to-plan
author: spec drafted by LUTZ (live-partner session); to be built in a normal Claude Code session
date: 2026-06-02
scope: Capture run-of-play state at entry-fill and exit-fill, stapled to the bet,
       so finished trades can be reviewed for exit-timing patterns. Logbook feature.
---

# Trade Snapshots for Exit Post-Mortems

## Why

The app's whole edge is *selling the swing, not riding to settlement*. To learn whether
we're actually doing that, we need to look back at closed trades and see **where the game
state was when we entered and exited** — was the exit at the peak of the move, or did we
ride past it? Did we bail on pressure (possession) instead of real chances (shots on
target)?

Right now that's impossible. The live run-of-play feed (score, clock, shots, SOT,
possession, per-shot stream, etc.) exists only **while a game is live in the feed**. The
second the game ends and the WS closes, it's gone and unrecoverable. The ledger keeps the
*fill* (entry/exit price, P&L, strategy tag) but not the *game state* at the fill moment.

This feature freezes the game state at entry and at exit, attaches it to the bet, and
nothing else. Two snapshots per trade. That's the entire feature.

**Concrete payoff:** the `mean_confirmation` book is currently −88% net ROI (−$95.72 over 7
bets) — the single thing dragging the whole account negative. The working hypothesis is
that these are favorite-lead plays held too long into the 75–90' danger window. Snapshots
let us *prove or disprove* that from data instead of asserting it: line up the exit-minute
game state across those 7 trades and see if the pattern is real.

## Scope decisions (already made — do not re-open)

1. **Auto-capture on fill.** No manual "mark this moment" button. When an entry or exit
   fill lands, snapshot the current run-of-play automatically. Zero extra user clicks.
2. **lutz.bot-logged trades only.** Snapshots attach only to bets in our ledger. Fills
   placed directly on kalshi.com (which are recorded as `bet_fill` rows with `bet_id=NULL`
   per `feedback_no_external_fill_reconciliation`) get **no** snapshot — there's no bet to
   attach to, and reopening external reconciliation is explicitly out of scope.
3. **Retrospective only.** This is a logbook artifact read *when the user asks "how have my
   exits been."* It is **NOT** consumed by LUTZ's live reads — those still pull fresh state
   every time. The "memory is dissolved by design" rule in
   `.claude/skills/lutz-partner/SKILL.md` stands. Do not wire snapshots into the
   `/partner/context` live path or any in-game suggestion logic.

## The critical timing constraint — read before designing

**The snapshot MUST be captured on the WS fill event, not the REST fills sweep.**

Fills reach the app two ways (see `backend/src/services/fills_sync.py`):
- **WS fill event** — real-time, fires at the actual fill moment. The game is still live in
  the feed; the run-of-play is in app memory *right now*.
- **REST `/portfolio/fills` sweep** — runs on a 30s timer, only to backfill fees. By the
  time it runs the game may be over and the run-of-play gone. **Too late to snapshot.**

So the capture hook hangs off the **WS fill path**, at the moment the fill event is
processed and matched to a bet. If for some reason the run-of-play isn't available at that
instant (game not in the live feed — e.g. a pre-match fill before kickoff, or a fill on a
market whose game-state poller isn't running), **capture what's available and mark the rest
null. Do not block the fill, do not retry, do not fabricate.** A pre-match entry legitimately
has no run-of-play — that's a null snapshot body with just the market mid, and that's fine.

## Where the seams are (verified against the tree on 2026-06-02)

- **WS fill handling:** `backend/src/kalshi/ws.py` + `backend/src/supervisor.py` (grep
  `OrderFilled` / `fill`). This is where a WS fill is received and routed. The bet-binding
  for a fill happens here / in `bet_service`. The snapshot capture should fire here, *after*
  the fill is matched to a bet, with the bet id in hand.
- **Run-of-play source:** the live game state is assembled by the events route
  (`backend/src/api/routes/events.py`, `get_event`) and the partner route composes it into
  `/partner/context` (`backend/src/api/routes/partner.py`). Find where the live run-of-play
  object lives in app state (the ingestion layer / app state populated by the score poller)
  and **copy from that same source** so the snapshot is byte-identical to what the site and
  LUTZ saw live. Do not build a second, parallel game-state assembler.
- **Market mid / price tape:** `_price_series` in `partner.py` and the price-history service
  (`backend/src/services/price_history.py`) — reuse for the price portion of the snapshot.
- **Models live one-per-file** in `backend/src/models/` (`bet.py`, `bet_fill.py`,
  `game.py`, `market.py`, `position.py`, …). New model goes in its own file.

## Data model

New table, one file: `backend/src/models/trade_snapshot.py`.

| column                | type            | notes                                                        |
|-----------------------|-----------------|--------------------------------------------------------------|
| `id`                  | int PK          |                                                              |
| `bet_id`              | int FK → bet.id | `ondelete="CASCADE"` — snapshots die with the bet            |
| `phase`               | str             | `'entry'` or `'exit'` (CheckConstraint)                      |
| `captured_at`         | datetime tz     | when the snapshot was taken (the fill moment)                |
| `game_minute`         | int \| null     | clock at capture; null if no live game state                 |
| `score_home`          | int \| null     |                                                              |
| `score_away`          | int \| null     |                                                              |
| `run_of_play_json`    | JSON \| null    | frozen run-of-play blob (see below); null for pre-match fill |
| `market_mid_cents`    | int \| null     | top-of-book mid at capture                                   |
| `price_history_json`  | JSON \| null    | the short mid tape around the fill                           |
| `created_at`          | datetime tz     | server_default now()                                         |

Constraints / indexes:
- `CheckConstraint("phase IN ('entry','exit')")`
- `Index` on `bet_id`
- Consider `UniqueConstraint(bet_id, phase)` **only if** a bet can have exactly one entry
  and one exit snapshot. NOTE: a bet's exit can fragment into multiple sells at different
  minutes (see `bet_fill.py` — "sold 40, then 60"). Decide deliberately:
  - **Option A (recommended):** snapshot only the **first** entry fill and the **first**
    exit fill per bet → unique on `(bet_id, phase)` holds. Simplest, and the first exit is
    usually the decision moment.
  - **Option B:** snapshot every fill → drop the unique constraint, `phase` derived from
    fill `action` (buy=entry / sell=exit). More rows, richer multi-sell picture, more
    storage. Probably YAGNI for v1.
  Pick A unless there's a clear reason for B. Flag the choice in the PR.

**`run_of_play_json` contents** — freeze exactly the live run-of-play fields LUTZ reads, so
post-mortem sees what was on screen: per-side shots, shots on target, possession, corners,
cards, saves, penalties-taken, last events, and the **per-shot stream** up to that minute
(minute / side / quality / location). Serialize the same object the live path produces;
don't hand-pick a subset that drifts from the live shape.

Bound check: `run_of_play_json` for one moment is small (tens of shots max). Two snapshots
per bet. 31 bets today → ≤62 rows. A full World Cup of traded games is low hundreds of rows.
No TTL, no eviction job needed — **the FK cascade is the lifecycle.** Delete the bet, the
snapshots go. This is the bounded-by-construction property; preserve it.

## Capture logic

In the WS fill handler, after the fill is matched to a bet:

1. Determine `phase`: buy → `entry`, sell → `exit`.
2. (If Option A) skip if a snapshot for `(bet_id, phase)` already exists — first fill wins.
3. Pull the current live run-of-play for that market's event from app state (the same source
   the events/partner route reads). If present, serialize it into `run_of_play_json`,
   `game_minute`, `score_home`, `score_away`. If absent (pre-match / no poller), leave those
   null.
4. Pull the current market mid + recent price tape (reuse `price_history` service) →
   `market_mid_cents`, `price_history_json`.
5. Insert the `trade_snapshot` row. **Never block or fail the fill on snapshot error** —
   wrap capture so a snapshot exception is logged and swallowed; the fill path is money and
   must not break for a logbook nicety.

## Read path

A read-only endpoint for post-mortem review, e.g.
`GET /api/ledger/bets/{bet_id}/snapshots` (or fold into the existing bet detail serializer
in `ledger.py` — `_bet_to_dict`). Returns the entry and exit snapshots for a bet.

This is what LUTZ reads when the user asks "how have my exits been" — by then it's a normal
ledger query, not a live-feed call. LUTZ would pull a closed bet's snapshots and compare
entry vs exit game state against the strategy docs (sold the swing vs rode past the peak;
SOT trajectory; whether the exit was inside the 75–90' danger window).

## Out of scope (do not build)

- No manual "mark this moment" capture.
- No snapshots for Kalshi-direct (external) fills.
- No whole-game tape for games that were never traded.
- No live-path consumption — `/partner/context` and suggestion logic are untouched.
- No TTL / eviction subsystem — the FK cascade is the only lifecycle.
- No backfill of existing closed bets — their run-of-play is already gone; nothing to capture.

## Acceptance

- A live entry fill writes one `entry` snapshot with non-null run-of-play.
- A live exit fill writes one `exit` snapshot.
- A pre-match (no live game) fill writes a snapshot with null run-of-play and a non-null
  market mid — and does **not** error.
- Deleting a bet removes its snapshots (cascade).
- A snapshot-capture exception never breaks or delays the fill path (logged + swallowed).
- The read endpoint returns entry+exit snapshots for a closed bet.
- Money path (cents-only, integer) untouched; no floats introduced for prices.
