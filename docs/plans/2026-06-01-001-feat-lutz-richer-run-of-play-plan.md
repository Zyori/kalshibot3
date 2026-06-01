---
title: "feat: Richer run-of-play for LUTZ — ESPN /summary shot feed + price-history buffer"
type: feat
status: complete
date: 2026-06-01
origin: docs/plans/2026-05-31-001-feat-ai-partner-skill-cockpit-plan.md
---

# Richer run-of-play for LUTZ — ESPN /summary shot feed + price-history buffer

## Summary

Close the four data gaps LUTZ named during the U9 dry run by mining a richer
ESPN endpoint we already have access to (`/summary?event=`, per-game) and by
keeping a short in-memory price series off the orderbook ticks we already
receive. The `/summary` feed adds a **per-shot stream** — minute, side, quality
(saved/missed/blocked/woodwork/goal), and coarse location (inside/outside box) —
plus boxscore stats we don't pull today (saves, blocked shots, penalty-kicks-
taken). The price buffer gives LUTZ the **trajectory** of a position's market
("47¢ climbing from 30" vs "47¢ falling from 55"), not just the snapshot. Both
surface through the existing `GET /partner/context` endpoint and require a
rewrite of the persona's data-fidelity boundary, which currently tells LUTZ
these signals are absent.

---

## Problem Frame

In the U9 dry run, LUTZ flagged four run-of-play gaps that cost real edge: (1)
no shot quality — "1 on target" hides a cleared tap-in vs a 30-yard screamer;
(2) no shot timing — cumulative totals can't show a chance cluster forming
*now*; (3) no penalty/big-chance signal — the ledger already carries two
`mean_confirmation` losses tagged "annoying pk"; (4) no price trajectory on a
position — LUTZ reconstructs the tape from memory across pulls, the exact
stale-memory trap the skill was built to avoid. He ranked them
xG/quality > timing > price-history > penalty, noting the first two change the
read on *every* game.

Research this session (verified live against AUT–TUN, ESPN event `401856597`,
slug `fifa.friendly`) found that gaps #1/#2/#3 are largely answerable from a
free ESPN endpoint we simply don't call yet, and #4 needs no external source at
all — it's a buffer over data already flowing in.

---

## Requirements

- R1. A per-shot event stream is available to the partner for a live soccer
  game: each shot carries minute, side (home/away), quality
  (`goal|saved|missed|blocked|woodwork|unknown`), coarse location
  (`inside_box|outside_box|null`), and the **raw commentary text** it was parsed
  from. (Gaps #1, #2.)
- R2. New boxscore stats not currently pulled — `saves`, `blocked_shots`,
  `penalty_kicks_taken`, `penalty_goals` — are available per side. (Gap #3,
  coarse penalty signal.)
- R3. A short in-memory price series (recent mid-prices, timestamped) per
  WS-subscribed soccer market is available to the partner, showing trajectory.
  In-memory, ephemeral, **not** persisted (it's session context, not money).
  (Gap #4.)
- R4. All of the above surface through `GET /partner/context` (event scope for
  the shot feed + boxscore; both scopes for price history on held/relevant
  markets), in flat readable form a model reads top-to-bottom.
- R5. The shot-feed parse is **read-time enrichment over a raw-text floor**: an
  unmatched commentary phrase degrades to `quality=unknown`/`location=null` with
  the raw text retained, and is logged — it never rejects the payload, never
  breaks the game read, never breaks the feed. (Explicitly NOT the all-or-
  nothing validation that caused the `inactive`-status feed outage.)
- R6. The persona's data-fidelity boundary in `.claude/skills/lutz-partner/
  persona.md` is rewritten to reflect what is now available (coarse shot
  quality/location/timing, saves, penalty-taken counts, price trajectory) vs
  what remains truly absent (true xG values, a real-time "penalty about to be
  taken" alert, posts/big-chances ESPN doesn't ship).
- R7. Cross-market isolation holds: only soccer games, only the user's tracked
  series; the `/summary` poll reads the live set the existing discovery/ESPN
  layer already scopes to soccer.
- R8. No new external provider, no recurring cost, no programmatic LLM. Same
  provider (ESPN), richer endpoint. (Paid xG feeds — API-Football, Sportmonks,
  Opta — were rejected on the same recurring-cost grounds as the LLM API.)

**Origin reference:** This intentionally relaxes the
`docs/plans/2026-05-31-001-feat-ai-partner-skill-cockpit-plan.md` scope boundary
"No new run-of-play data sources" — but stays within the same provider (ESPN),
so it's a smaller break than the deferred paid-feed / news-feed work that origin
flagged as needing its own plan.

---

## Scope Boundaries

- **No new external provider.** ESPN only. No API-Football/Sportmonks/Opta, no
  scraping (Understat etc.). The recurring-cost line that killed paid xG and the
  LLM API holds.
- **No true xG.** ESPN's free feed has no xG *value*. The shot quality/location
  tags are a coarse proxy, and the persona must say so — LUTZ reports "outside-
  box blocked attempt," never an invented xG number.
- **No real-time pending-penalty alert.** We see a penalty *after* it appears in
  the feed (PK-taken count, or a commentary line), not a "ref is pointing to the
  spot right now" signal. ESPN doesn't ship that; the persona boundary says so.
- **No price-history persistence.** The buffer is in-memory and dies with the
  process (mirrors the nudge de-dup decision). A restart loses the trajectory;
  worst case is LUTZ sees only the snapshot until the buffer refills — no money
  or safety impact. Persisting it would add a table + write path + eviction for
  zero benefit.
- **Soccer only.** NFL/UFC and any non-soccer commentary parsing are out. The
  commentary patterns are soccer-specific ("attempt", "box", "header"); other
  sports get their own work if ever.
- **No new run-of-play *metrics* beyond what /summary already carries.** We
  parse what's in the commentary and boxscore; we do not compute derived metrics
  (rolling xG, momentum indices). LUTZ does the reading.

### Deferred to Follow-Up Work

- **Shot-location coordinates** (`keyEvents.fieldPositionX/Y`): present but
  inconsistent in the payload (zero on many events). Text location
  (inside/outside box) is the reliable source this phase. If coordinates prove
  reliable on more games later, they could upgrade location to a real heat-map —
  separate work.
- **Per-shot "big chance" grading** beyond the box/quality heuristic: a learned
  or rules-based danger score. Out of scope; LUTZ grades from the raw tags.
- **NFL/other-sport play-by-play**: same /summary mechanism could feed other
  sports, but the parsing is sport-specific. Defer until a second sport is live.

---

## Context & Research

### Relevant Code and Patterns

- `backend/src/ingestion/espn_scoreboard.py` — the existing ESPN poller. Carries
  `TeamStats` (shots, shots_on_target, possession, corners, fouls, cards),
  `MatchEvent` (kind/minute/player/side/text), `EspnEvent` (with **`espn_id`** —
  already the per-game id needed for `/summary?event=`), `_parse_team_stats`,
  `_enrich_with_details`, `_classify_detail`, and `run()`/`_refresh_once()` with
  **adaptive cadence** (`POLL_INTERVAL_LIVE_S = 40`, `POLL_INTERVAL_IDLE_S =
  1800`). The `/summary` fetch extends this loop, live-games-only.
- `backend/src/api/routes/partner.py` — `GET /partner/context` composes from
  `events.get_event` (run-of-play backbone), `positions.list_positions`,
  `ledger._bet_to_dict`. The shot feed + boxscore ride along inside the event
  payload (`get_event` already embeds ESPN state); price history is added to the
  positions/markets shape.
- `backend/src/api/routes/events.py` — `_live_payload`, `_team_stats_dict`,
  `_match_event_dict` serialize ESPN state into the event response. The shot
  feed + new boxscore stats serialize here so both `/events` (site) and
  `/partner/context` (LUTZ) see identical data — single source of truth.
- `backend/src/services/nudge_evaluator.py` — the in-memory, process-lifetime
  state pattern (a `set`/`dict` owned by one supervisor-held instance, reset on
  subject disappearance). The price buffer mirrors this: a small class holding a
  bounded `deque` per ticker, fed from the WS book path, read by the context
  endpoint.
- `backend/src/core/ws_manager.py` / the WS book path — where orderbook
  snapshots/deltas land. The price buffer samples the mid here (or on a short
  timer reading the current book) — decide the sampling seam at implementation
  (see Deferred to Implementation).
- `.claude/skills/lutz-partner/persona.md` — the data-fidelity boundary section
  (currently: "You see shots-on-target, cards, score, clock — NOT xG, saves,
  posts, big-chances, or explicit penalties") to rewrite per R6.

### Institutional Learnings

- **The `inactive`-status feed outage (this session, committed `a277b64`):** one
  bad market status failing a whole-series validation dropped the entire
  friendlies feed. The shot-feed parse must be the opposite shape — read-time
  enrichment that degrades per-item, never an all-or-nothing gate (R5).
- **Cross-market isolation** (`feedback_cross_market_isolation`): the `/summary`
  poll reads only the live soccer set the existing layer scopes; never touches
  non-soccer.
- **Orderbook delta is fractional** (`project_orderbook_delta_fractional`): the
  live book is already maintained correctly; the price buffer samples the
  *resolved* mid, not raw deltas — it consumes the same value `/events` shows.
- **Timezone normalize to Eastern** (`feedback_timezone_normalize_eastern`): shot
  minutes are match-clock (not wall-clock), so no TZ concern there; any wall-clock
  timestamp on price samples that surfaces to display follows Eastern-at-boundary
  (the buffer stores monotonic/UTC, formats at the read edge if shown).

### External References

- ESPN `site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary?event={id}` —
  verified live this session. Carries `commentary` (~80 items/game, each with
  `time.displayValue` + templated shot text), `keyEvents`, and `boxscore.teams[].
  statistics` (Saves, Blocked Shots, Penalty Kicks Taken, Penalty Goals). The
  shot-text stems (`Attempt saved.` / `Attempt missed.` / `Attempt blocked.` /
  `hits the bar|post`) are machine-generated (provider `SA.ENVOY` = Stats
  Perform), so templated and stable — low brittleness on quality, medium on the
  more varied location phrasing.

---

## Key Technical Decisions

- **Extend `espn_scoreboard`'s existing loop for `/summary`, do not add a second
  supervisor task.** The scoreboard poller already computes the live-game set and
  already polls at 40s when games are live — exactly the cadence and scoping the
  `/summary` fetch needs. A separate task would duplicate the live-set
  computation and add cross-task coordination for no benefit. The simplicity rule
  in CLAUDE.md favors one poller, one cadence. (`espn_id` is already on
  `EspnEvent`, so no new id resolution.)
- **Parse quality + location from commentary text, over a raw-text floor.** The
  structured tags are best-effort enrichment; the raw commentary sentence is
  always retained. An unmatched phrase → `quality=unknown`/`location=null` +
  raw text + a debug log of the unmatched phrase (self-reporting gaps to tighten
  patterns over time). This caps the downside at "a shot is occasionally
  ungraded," never a break. (R5; the anti-pattern is the `inactive` outage.)
- **Shot quality is a small closed enum; location is a bucket with an explicit
  unknown.** `goal|saved|missed|blocked|woodwork|unknown` for quality;
  `inside_box|outside_box|null` for location. Keeps LUTZ's read graded but
  bounded — no free-form quality strings to reason over inconsistently.
- **The shot feed serializes in `events.py` alongside existing ESPN state**, so
  `/events` (site) and `/partner/context` (LUTZ) are the same bytes — single
  source of truth, and the site could later render it too with no partner-side
  change.
- **Price buffer is in-memory, bounded, process-lifetime** — mirrors
  `nudge_evaluator`. A `deque(maxlen=N)` of `(timestamp, mid)` per subscribed
  ticker, capped (N small — enough to show trajectory, e.g. last ~20 samples).
  Bounded by construction (no unbounded growth — a steady-state concern the
  project rules call out); keys for closed/unsubscribed markets are dropped.
- **Sampling the mid is decoupled from the WS delta firehose.** The buffer does
  not append on every fractional delta (too noisy, unbounded write rate);
  it samples the resolved mid on a modest interval (or on a coalesced book-change
  signal). Exact seam decided at implementation against the live WS path.

---

## Open Questions

### Resolved During Planning

- **Free source for shot quality/timing/penalty?** Yes — ESPN `/summary`,
  verified live. No paid feed, no scraping.
- **How does the poll know the game id?** `EspnEvent.espn_id` already exists; the
  live set is already computed by the scoreboard poller.
- **One poller or two?** One — extend `espn_scoreboard` (see Key Decisions).
- **How brittle is text parsing?** Quality: low (templated provider text).
  Location: medium (more phrasing variety) but degrades to `null`, never breaks.
  Raw-text floor + unmatched-logging cap the risk.
- **Persist price history?** No — in-memory, ephemeral, like nudge state.

### Deferred to Implementation

- **Price-sample seam:** append on a coalesced book-change event vs a short timer
  reading the current book. Decide against the live `ws_manager`/book path — pick
  whichever avoids per-delta noise without missing real moves.
- **Buffer depth N and sample interval:** tune to "enough to read the tape" without
  bloating the context payload (start ~15–20 samples; adjust against a live game).
- **Exact commentary patterns:** the full phrase set for quality stems and
  location buckets — seed from the verified AUT–TUN set, expand from the
  unmatched-phrase log over real games.
- **Shot-feed length in the payload:** whole-game vs last-N-shots in
  `/partner/context` — trim to what's useful in a terminal read (likely full game,
  it's bounded ~20–30 shots, but confirm payload size).
- **Whether `_classify_detail` is extended or a sibling parser is added** for the
  commentary stream (scoreboard `details` and summary `commentary` are different
  arrays with different shapes — likely a sibling parser, decide at impl).

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
  EXISTING (per-league, 40s live)            NEW (per-live-game, same loop)
  ┌──────────────────────────────┐          ┌────────────────────────────────┐
  │ espn_scoreboard._refresh_once│          │ for each LIVE soccer game:      │
  │  GET /{slug}/scoreboard      │──────────│  GET /{slug}/summary?event=ID   │
  │  -> TeamStats, MatchEvent,   │  live set │  -> parse commentary -> shots[] │
  │     EspnEvent(espn_id)       │  + espn_id│     (minute,side,quality,loc,   │
  └──────────────────────────────┘          │      raw_text)                  │
                                             │  -> boxscore -> saves, blocked, │
                                             │     pk_taken, pk_goals          │
                                             └────────────────────────────────┘
                 │                                          │
                 ▼                                          ▼
        EspnEvent snapshot  ◀── shots[] + new boxscore stats attached
                 │
                 ▼
  events.py _live_payload  ──serializes──▶  GET /events  AND  GET /partner/context
                                                 (same bytes — single source)

  PRICE BUFFER (in-memory, mirrors nudge_evaluator)
  ┌─────────────────────────────────────────────────────────┐
  │ WS book mid (resolved) ──sample(interval)──▶              │
  │   {ticker: deque[(ts, mid)], maxlen=N}                    │
  │   drop keys for closed/unsubscribed markets               │
  └─────────────────────────────────────────────────────────┘
                 │ read
                 ▼
  GET /partner/context ── price_history: [{ts, mid}, …] per held/relevant market
```

---

## Implementation Units

- U1. **ESPN `/summary` fetch + commentary/boxscore parse**

**Goal:** Fetch `/summary?event={espn_id}` for each live soccer game and parse
it into a per-shot stream + the new boxscore stats, over a raw-text floor.

**Requirements:** R1, R2, R5, R7, R8

**Dependencies:** None

**Files:**
- Modify: `backend/src/ingestion/espn_scoreboard.py` (extend `TeamStats` with
  `saves`, `blocked_shots`, `penalty_kicks_taken`, `penalty_goals`; add a
  `ShotEvent` dataclass `(minute, side, quality, location, raw_text)`; add a
  commentary parser + the `/summary` fetch into the live branch of
  `_refresh_once`/`run`)
- Test: `backend/tests/test_ingestion/test_espn_summary_parse.py`

**Approach:**
- In the live branch only (where `POLL_INTERVAL_LIVE_S` already applies), for
  each live `EspnEvent`, GET `/{slug}/summary?event={espn_id}`. Attach parsed
  `shots: list[ShotEvent]` and the new boxscore stats to the `EspnEvent`
  snapshot.
- Commentary parser: iterate `commentary[]`; for each item read
  `time.displayValue` (minute), classify the text into a `ShotQuality` enum by
  templated stem (`Attempt saved.`→saved, `Attempt missed.`→missed, `Attempt
  blocked.`→blocked, `hits the bar|post|woodwork`→woodwork, goal→goal; no
  match→ skip non-shot lines), bucket location (`outside the box|long range`→
  outside_box; `inside the box|centre of the box|six yard|close range`→inside_box;
  else null), resolve side from the team name in the text against home/away.
  **Always keep `raw_text`.** Log unmatched-but-shot-like phrases at debug.
- Boxscore: map the labels we don't pull yet (`Saves`, `Blocked Shots`, `Penalty
  Kicks Taken`, `Penalty Goals`) into the extended `TeamStats`, same shape as the
  existing `_parse_team_stats` mapping.
- A `/summary` fetch failure for one game logs and is skipped — never breaks the
  scoreboard refresh (mirror the existing `espn_fetch_failed` per-slug handling).

**Patterns to follow:** `espn_scoreboard._parse_team_stats` (label→field map),
`_enrich_with_details`/`_classify_detail` (event classification), the per-slug
`try/except` in `_refresh_once` (one failure doesn't kill the loop), `EspnEvent`
dataclass shape.

**Test scenarios:**
- Happy path: a fixture `/summary` payload (the verified AUT–TUN shape) parses to
  the expected shot list — correct count, minutes, sides, quality, location.
- Happy path: boxscore labels map to `saves`/`blocked_shots`/`penalty_kicks_taken`/
  `penalty_goals` per side.
- Edge case: an `Attempt saved.` line with no recognizable box phrase →
  `quality=saved, location=null`, `raw_text` retained.
- Edge case: a shot-like line with a novel phrasing that matches no quality stem
  → not crash; either skipped (non-shot) or `quality=unknown` with raw_text +
  debug log. (R5 — degrade, don't break.)
- Edge case: pre-match / empty `commentary` → `shots=[]`, no error.
- Error path: `/summary` HTTP failure for one game → that game's shots stay empty,
  the scoreboard refresh for other games still completes.
- Edge case: side resolution when the team name in the text matches neither
  home nor away (substitute spelling) → `side=None`, shot still recorded.

**Verification:** Against a live game, the poller attaches a shot list whose
minutes/quality/location match the commentary, and the boxscore saves/penalty
counts match ESPN's box. A deliberately mangled commentary line produces an
ungraded-but-recorded shot, not an exception.

---

- U2. **Surface shot feed + new boxscore stats through the event serializer**

**Goal:** Serialize the shot stream and new boxscore stats into the event payload
so `/events` and `/partner/context` both expose them, flat and readable.

**Requirements:** R1, R2, R4, R7

**Dependencies:** U1

**Files:**
- Modify: `backend/src/api/routes/events.py` (`_team_stats_dict` gains the new
  boxscore fields; `_live_payload` gains a `shots` array serialized from
  `EspnEvent.shots`)
- Test: `backend/tests/test_api/test_event_shot_feed.py`

**Approach:**
- Extend `_team_stats_dict` with `saves`, `blocked_shots`, `penalty_kicks_taken`,
  `penalty_goals` (None when ESPN didn't ship them, same convention as existing
  stats).
- Add `shots` to `_live_payload`: a flat list of `{minute, side, quality,
  location, text}` ordered by event order. Keep it readable for a terminal —
  no nested ceremony (matches the partner-context design goal).
- No new endpoint — this rides the existing event payload that
  `/partner/context` already composes via `get_event`.

**Patterns to follow:** `events.py` `_team_stats_dict`, `_live_payload`,
`_match_event_dict` (flat serialization of ESPN state).

**Test scenarios:**
- Happy path: an event with parsed shots serializes a `shots` array with the
  right fields and order; boxscore stats appear under each side.
- Edge case: a game with no shots → `shots: []`, new boxscore fields null, not
  missing-key errors.
- Integration: `GET /partner/context?event=` returns the same `shots`/boxscore
  values as `GET /events/{ticker}` for the same game (single source of truth).

**Verification:** Hitting `/partner/context?event=` on a live game returns a
shot-by-shot list and saves/penalty counts a human reads top-to-bottom; the
numbers equal `/events` for the same game.

---

- U3. **In-memory price-history buffer**

**Goal:** Keep a short, bounded, per-market series of recent mid-prices off the
WS book, in memory, surfaced for the partner.

**Requirements:** R3, R4, R7, R8

**Dependencies:** None

**Files:**
- Create: `backend/src/services/price_history.py` (a small class: `deque(maxlen=N)`
  of `(timestamp, mid_cents)` per ticker; `record(ticker, mid)`; `series(ticker)`;
  `drop(ticker)`; soccer-only keys)
- Modify: the WS book seam (`backend/src/core/ws_manager.py` or the book-update
  path) to sample the resolved mid into the buffer on a modest interval / coalesced
  change — **not** per fractional delta
- Modify: `backend/src/supervisor.py` (own the buffer instance; drop keys when a
  market closes/unsubscribes, mirroring nudge-state reset)
- Test: `backend/tests/test_services/test_price_history.py`

**Approach:**
- `PriceHistory` owns `{ticker: deque[(ts, mid_cents)]}` with `maxlen=N`. `record`
  appends; `series` returns the list newest-or-oldest-first (decide at impl, be
  consistent). Money stays integer cents (the mid is `round((bid+ask)/2)`, cents).
- Sampling decoupled from the delta firehose (Key Decision): a short interval or
  coalesced book-change appends one sample, so write rate is bounded and the
  series shows trajectory, not noise.
- Keys for closed/unsubscribed markets are dropped (bounded growth — steady-state
  rule). Soccer-only: only subscribed soccer markets get sampled (the WS
  subscription set is already soccer-scoped).
- In-memory and ephemeral by design — no model, no migration, no persistence.

**Patterns to follow:** `backend/src/services/nudge_evaluator.py` (in-memory
state owned by a supervisor-held instance, reset on subject disappearance);
integer-cents rule (CLAUDE.md #1).

**Test scenarios:**
- Happy path: recording a sequence of mids returns them as a series in order,
  capped at `maxlen` (oldest evicted).
- Edge case: `series` for an unknown ticker → empty list, not error.
- Edge case: `drop` removes a ticker's series; a later `record` starts fresh.
- Edge case: mids stay integer cents (no float leak into the series).
- Integration: a closed/unsubscribed market's key is dropped so the buffer
  doesn't grow unbounded across a session (steady-state).

**Verification:** Over a few minutes on a live market, the buffer holds a bounded
recent mid series; an unsubscribed market's series is gone; no unbounded growth.

---

- U4. **Add price history to `/partner/context`**

**Goal:** Expose the price series in the context payload for held/relevant
markets so LUTZ reads the tape, not the snapshot.

**Requirements:** R3, R4

**Dependencies:** U3

**Files:**
- Modify: `backend/src/api/routes/partner.py` (read the buffer instance from
  `request.app.state`; attach `price_history` to the relevant markets/positions
  in both scopes)
- Test: `backend/tests/test_api/test_partner_price_history.py`

**Approach:**
- Read the supervisor-owned `PriceHistory` off `app.state` (same way bankroll is
  read off `app.state.kalshi_balance_cents`). For event scope, attach the series
  to each child market; for book scope, attach to each open position's market.
- Flat shape: `price_history: [{ts, mid_cents}, …]` (or `mid` formatted at the
  edge — keep cents in the payload, the partner reads cents). Empty list when the
  buffer has nothing yet (pre-subscribe, just-restarted).
- Best-effort: a missing buffer (no supervisor in tests) yields empty series, not
  a 500 — mirror the `broadcast` best-effort guard already in this file.

**Patterns to follow:** `partner.py` `_bankroll_cents` (read off `app.state`,
None-safe), the existing context composition.

**Test scenarios:**
- Happy path (event): a subscribed market with samples returns a `price_history`
  series alongside its top-of-book.
- Happy path (book): an open position's market carries its series.
- Edge case: no buffer / no samples → `price_history: []`, endpoint still 200.
- Integration: the mids in `price_history` are integer cents and consistent with
  the current top-of-book mid at sample time.

**Verification:** `/partner/context?event=` on a live held market shows a recent
mid series; LUTZ can state the trajectory ("47 climbing from 30") without
reconstructing from memory.

---

- U5. **Rewrite the persona data-fidelity boundary**

**Goal:** Update `persona.md` so LUTZ knows what he can now see (and still can't),
and uses the new signals without fabricating xG.

**Requirements:** R6, R8

**Dependencies:** U2, U4 (the data must exist before the persona claims it)

**Files:**
- Modify: `.claude/skills/lutz-partner/persona.md` (the data-fidelity boundary
  section; possibly a line in the threat-reading guidance)
- Optionally Modify: `.claude/skills/lutz-partner/SKILL.md` (note the new context
  fields — `shots[]`, new boxscore stats, `price_history` — in the context-pull
  description)

**Approach:**
- Rewrite the boundary: LUTZ now sees per-shot **timing**, coarse **quality**
  (saved/missed/blocked/woodwork), coarse **location** (inside/outside box),
  **saves**, **penalty-kicks-taken**, and **price trajectory**. He still does NOT
  see true **xG values**, a real-time **pending-penalty** alert, or
  posts/big-chances beyond what a commentary line mentions.
- Instruct: grade threat from the new shot tags (an outside-box blocked attempt ≠
  an inside-box saved one), read the tape from `price_history`, but **never invent
  an xG number** — report the qualitative tag, not a fabricated value. The "ask
  the user for broadcast texture when it would change the read" instruction stays
  for what's still absent.
- This is user-tunable persona content; the unit rewrites the *boundary facts*
  (what's available), the user can adjust voice. Keep the opinionated register.

**Patterns to follow:** the existing data-fidelity boundary block in `persona.md`;
the brevity/format conventions added earlier this session.

**Test scenarios:** *Test expectation: none — markdown persona/skill content, no
executable behavior. Validated by a live read in the next session (does LUTZ use
shot quality + trajectory and refuse to invent xG?).*

**Verification:** A reviewer reading the new boundary can tell exactly which
signals LUTZ may cite and which he must still flag as unseen; a live read uses
shot quality/location/timing and price trajectory without ever stating an xG
number.

---

## System-Wide Impact

- **Interaction graph:** `espn_scoreboard` gains a per-live-game `/summary` fetch
  inside its existing loop (no new task). `events.py` serializer grows two fields
  (`shots`, extended boxscore). A new `PriceHistory` service is owned by the
  supervisor, fed from the WS book seam, read by `partner.py`. No new endpoint, no
  new WS event, no new external provider.
- **Error propagation:** A `/summary` fetch or parse failure for one game is
  swallowed (logged) and never breaks the scoreboard refresh or other games (R5).
  A missing price buffer yields empty series, never a 500.
- **State lifecycle risks:** The price buffer must drop keys for closed/
  unsubscribed markets or it grows across a session (steady-state rule). Bounded
  `deque(maxlen=N)` caps per-ticker memory; key-drop caps ticker count. The shot
  parse must never accumulate unbounded state — it's recomputed each poll from the
  fresh payload.
- **API surface parity:** Shot feed + boxscore serialize once in `events.py`, so
  `/events` (site) and `/partner/context` (LUTZ) stay identical — no parallel
  serializer. The site can render the shot feed later with zero partner-side work.
- **Integration coverage:** `/partner/context` shot/boxscore numbers equal
  `/events` (U2 integration test); price-history mids consistent with top-of-book
  (U4 integration test).
- **Unchanged invariants:** Integer-cents money, soccer-only cross-market
  isolation, no programmatic LLM (`llm/client.py` stays empty), no autonomous
  trading, the existing scoreboard cadence, and the `/scoreboard` extraction are
  all unchanged. This adds *read context*, never an executor or a new provider.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| ESPN reword commentary stems → quality/location parse misses | Raw-text floor (R5): unmatched → `unknown`/`null` + retained text + debug log. Degrades per-shot, never breaks. Quality stems are templated (provider feed), low churn. |
| ESPN whole-payload shape changes (the `inactive` failure mode) | Parse is read-time enrichment, not a validation gate. A bad `/summary` is skipped for that game; the scoreboard refresh and feed are untouched. |
| Per-game `/summary` poll adds request volume | Live-games-only (bounded set, usually a handful), at the existing 40s live cadence; idle games never polled. No new external provider or cost. |
| Price buffer grows unbounded over a long session | `deque(maxlen=N)` per ticker + key-drop on close/unsubscribe; in-memory and ephemeral by design (steady-state rule). |
| Sampling the mid on every WS delta floods the buffer | Sample on interval / coalesced change, not per delta (Key Decision); write rate bounded. |
| Persona claims a signal the data doesn't actually carry | U5 depends on U2/U4 landing first; the boundary rewrite states only verified-available signals; "never invent xG" is explicit. |
| LUTZ over-trusts coarse location as if it were xG | Persona boundary frames it as a coarse proxy; report the tag, never a number. |

---

## Documentation / Operational Notes

- No new env vars, no new API key, no new external provider. ESPN is already a
  dependency; this calls a second endpoint on it.
- No new background service — the `/summary` fetch joins the existing
  `espn_scoreboard` loop; the `PriceHistory` service is a passive in-memory object
  on the supervisor, not a polling task.
- No migration — the price buffer is in-memory; the new boxscore fields and shot
  feed are transient ESPN-derived data, not persisted to the DB.
- Backend restart picks up the new poll + serializer + buffer; `vite build --watch`
  is irrelevant (no required frontend change — the site rendering the shot feed is
  deferred/optional).
- The unmatched-commentary-phrase debug log is the operational signal for tightening
  parse patterns over real games.

---

## Sources & References

- **Origin / relaxed scope:** `docs/plans/2026-05-31-001-feat-ai-partner-skill-cockpit-plan.md`
  (the "No new run-of-play data sources" boundary this intentionally relaxes,
  same-provider).
- **Research (this session, verified live):** ESPN `/summary?event=401856597`
  (slug `fifa.friendly`, AUT–TUN) — commentary shot stream + boxscore saves/
  penalties; `EspnEvent.espn_id` already present.
- **Key existing code:** `backend/src/ingestion/espn_scoreboard.py`,
  `backend/src/api/routes/{events,partner}.py`, `backend/src/services/nudge_evaluator.py`,
  `backend/src/core/ws_manager.py`, `backend/src/supervisor.py`,
  `.claude/skills/lutz-partner/{persona,SKILL}.md`.
- **Institutional learnings:** the `inactive`-status feed outage (commit `a277b64`,
  `docs/known-issues/`), `feedback_cross_market_isolation`,
  `project_orderbook_delta_fractional`.
