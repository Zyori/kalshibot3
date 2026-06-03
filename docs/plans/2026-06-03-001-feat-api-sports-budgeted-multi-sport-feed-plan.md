# API-Sports — budgeted, multi-sport data feed

**Status:** ⏸ PARKED for soccer (2026-06-03) — core premise (WC xG) UNCONFIRMED. A live
probe found no xG for *2022* WC, but whether *2026* WC gets it is genuinely open;
decided by a hard re-probe in WC week 1 (see below). Budget-core design reusable for
UFC/NFL now. **No code written.**
**Date:** 2026-06-03
**Owner:** Zyori

---

## ⛔ FINDING THAT KILLED THE SOCCER CASE (2026-06-03, verified by live probe)

Before writing any code, we probed API-Sports with a live key. The result falsified
the central premise ("pay for World Cup xG that ESPN can't give us"):

- **World Cup fixtures carry NO xG.** Pulled the completed 2022 WC final
  (Argentina v France, fixture 979139) via `/fixtures/statistics` on the free key:
  the `expected_goals` field is **absent entirely** — not null, not gated, not there.
  The stats returned (total shots, shots on/off goal, inside/outside box, possession,
  corners, saves, passes) **duplicate what the existing ESPN pipeline already extracts
  for free.**
- **The "too early" question is GENUINELY OPEN — not settled.** Two readings, and
  today's data can't distinguish them:
  - *Cautious:* 2022 WC (finished, full stats coverage) has no xG → WC just isn't a
    competition they compute it for.
  - *Optimistic (Zyori's, and it's a fair prior):* xG is a model run over shot data,
    not a per-competition capability — they clearly HAVE the model (EPL proves it), so
    applying it to WC is a coverage decision they may well flip on for 2026, the
    biggest event in the world. A 2022 fixture reflects their *2022* coverage, not
    their 2026 plans; providers expand coverage over time.
  - **The real swing factor is data RIGHTS, not prestige.** xG needs granular
    shot-location data (Opta/Stats Perform-class). FIFA international rights are often
    a separate, pricier bundle than domestic leagues — which can make a mid-tier
    aggregator deep on EPL but thin on WC *because* the WC feed costs more. So prestige
    can cut either way. **Verdict: unknown until real 2026 WC games can be probed.**
    Do NOT treat the 2022 absence as proof of a 2026 absence.
- **It is NOT a tier gate.** Proven by direct comparison: a 2023 EPL fixture
  (Burnley v Man City, fixture 1035037) on the **same free key** returns
  `expected_goals: 0.33 / 2.08`. So the API *does* compute xG — for club leagues,
  not for World Cup. Paying changes the request ceiling, not the field set.

**External-source check (2026-06-03):** there is **no free LIVE xG source for the World
Cup** anywhere. Free options (StatsBomb open-data, Understat, FBref, FootyStats) are
post-hoc research dumps or scrape-only webpages — not live-to-the-minute feeds.
Live WC xG is a premium product: Sportmonks "All-In" ~€129/mo, iSports ~$49/mo. The
real question is no longer "free vs paid xG" but "is €49–129/mo of live xG worth it for
a one-month tournament on a small bankroll" — a product call, deferred.

### What survives this finding

- **The budget-core architecture is still sound** IF API-Sports is ever used for a
  sport where its data is actually additive (UFC, NFL). The header-based auto-detect,
  per-sport quota model, FREE/FULL cadence dials, and `can_spend` clamp were all
  verified against real responses (headers present; `/status` is free; per-MINUTE cap
  is **10/min on free** — tighter than first assumed, binding during concurrent fetch).
- **The soccer-xG phase (Phase 3) is dead.** Do not build it. ESPN remains the soccer
  feed; it already provides equivalent stats plus a per-shot commentary stream.
- **The LUTZ latency-awareness fix and the ESPN event-burst** (`2026-06-03-002`) are
  **independent of this** and still worth doing — they fix an observed confusion, not
  an xG gap.

### ⏰ HARD RE-PROBE — World Cup week 1 (after ~June 11, 2026)

This is the decisive test, and it's the ONLY thing that settles the open question
above. Once real 2026 WC group games have been played, pull a live/finished 2026 WC
fixture and check `/fixtures/statistics` for `expected_goals`:

```bash
# (free key in .env as API_SPORTS_KEY)
# 1. get a played 2026 WC fixture id:
curl -s -H "x-apisports-key: $API_SPORTS_KEY" \
  "https://v3.football.api-sports.io/fixtures?league=1&season=2026&from=2026-06-11&to=2026-06-14"
# 2. check that fixture for the expected_goals field:
curl -s -H "x-apisports-key: $API_SPORTS_KEY" \
  "https://v3.football.api-sports.io/fixtures/statistics?fixture=<ID>"
```

- **If `expected_goals` is present** → Zyori was right, coverage was just not live yet.
  The soccer-xG case REOPENS — reconsider the sub for the WC window (now a clean
  product call: live xG worth €X/mo for ~1 month?). The budget-core design here is
  ready to build against.
- **If still absent on a played 2026 fixture** → now it's settled: WC genuinely has no
  xG on this API. Soccer-xG stays dead; ESPN remains the feed.

Until that probe runs, this plan is *parked, not killed* for soccer. **Re-scope:** if
UFC/NFL work begins first, lift the budget core and build that sport surface
concretely regardless of how the WC-xG question lands.

---

*(Original plan retained below for the budget-core design, which is reusable for
non-soccer sports. Everything soccer-xG-specific in it is superseded by the finding
above.)*

## Problem

ESPN's free endpoints are a solid MVP live feed for soccer, but they have two gaps
the workbook needs to close:

1. **No xG.** ESPN ships shots / shots-on-target, not expected goals. xG is the
   single most useful in-game signal for soccer value calls.
2. **Thin non-soccer coverage.** Free ESPN endpoints are weak for UFC/NFL/MLB/NBA —
   notably UFC, which most sports APIs omit entirely.

[API-Sports](https://api-sports.io/) covers all five of our target sports under one
account, including soccer xG, and has a free-forever tier. We want to use it
**surgically** — for the data ESPN can't give us — without ESPN's strengths or our
budget getting wasted, and without spend "getting out of control."

## Confirmed facts (verified, not assumed)

- **Quota is per-API (per-sport), not per-account.** Free = 100 requests/day *each*
  for football, MMA, NFL, etc. Resets 00:00 UTC; unused requests are lost.
  ([API-Sports](https://api-sports.io/))
- **Free vs paid is volume + historical range, NOT features.** Every endpoint
  (including xG) is on the free tier. The only differences are the daily ceiling
  (100 → 7.5k → … → 150k) and how far back history goes.
  ([API-Sports](https://api-sports.io/))
- **No overage billing.** Exhausting the quota returns errors (HTTP 429 / empty
  `response` with an `errors` body) — it does NOT charge you. The danger is *silent
  starvation*, not surprise cost.
- **Every response carries the budget on the wire:**
  `x-ratelimit-requests-limit` (today's ceiling) and
  `x-ratelimit-requests-remaining` (left today), plus per-minute
  `X-RateLimit-Limit` / `X-RateLimit-Remaining`.
  ([API-Football ratelimit docs](https://www.api-football.com/news/post/how-ratelimit-works))
- **`/status` endpoint returns plan name + quota and does NOT count against the
  daily quota.** This is the free, truthful "which subs do we have" source.
- **2026 World Cup: June 11 → July 19** (~1 month, up to 4 games/day, sometimes 3
  concurrent). One month of sub; ~$30 to break even.

## Design principles this plan commits to

Three **independent dials**, never conflated:

1. **Ceiling — auto-detected, truthful.** Read from `x-ratelimit-requests-limit` /
   `-remaining` on every response, and from `/status` on a slow heartbeat (free).
   The app always knows the real budget. Sub or unsub → ceiling moves on the next
   request, no config to edit or forget.
2. **Mode — manual intent (FREE | FULL), per sport.** Mode is **how aggressively we
   spend the budget we have** = poll *cadence*, not *what data we fetch* (there's
   nothing to gate — all endpoints are on free). FULL = aggressive live polling
   (xG ~every 5 min during a live game). FREE = lazy / on-demand only.
3. **Clamp — mode is bounded by ceiling.** A fat-fingered FULL on a free-tier sport
   can never 429-starve the feed: the budgeter refuses to schedule a poll that would
   cross a safety margin below `remaining`. The ceiling is the guardrail on intent.

Workflow this produces: *bump the sub on api-sports.io → app auto-sees the higher
ceiling → flip that sport's mode to FULL → poller spends the new headroom.* Three
observable, separable steps.

### Why ESPN stays (feed model = "alongside", Option A)

ESPN already does the **expensive high-frequency work** (score/clock/shots every 40s)
for free and unlimited. API-Sports' value is *only* the two things ESPN can't do.
So we spend the metered budget exclusively on those:

```
                  ┌─ ESPN /scoreboard + /summary ──→ score, clock, shots, cards
   Live soccer ───┤   (40s, free, unlimited)          [unchanged — existing pipeline]
                  └─ API-Sports /fixtures?live ─────→ xG only
                      (sparse: ~every 5 min, FULL)    [~15–30 req/game, not ~150]

   UFC (now) ──────── API-Sports ──────────────────── full live data (ESPN weak here)
   NFL/MLB/NBA ────── scaffolded, wired later
```

Cost comparison that drives this (one 2.5h soccer game):

| Feed | Cadence | Reqs/game | vs 100/day free |
|---|---|---|---|
| ESPN | 40s | ~225 | free + unlimited — N/A |
| API-Sports as live feed | 60s | ~150 | blows the whole day on one game |
| **API-Sports xG-only (this plan)** | ~5 min | **~15–30** | several games/day fit in free |

xG is a cumulative match stat — polling it on **10–15 min ticks** loses nothing real
(xG barely moves between shots). This keeps the free tier genuinely useful (so "drop
to free after WC" is a real cadence change, not a feed blackout) and preserves the
working `espn_scoreboard.py` pipeline untouched.

### The 4-WC-games-in-a-day stress test (the load case that matters)

xG request count is driven by **game-hours, not concurrency** — 4 games is 4 games'
worth of polls whether staggered or stacked. At 10–15 min ticks, ~2.5h/game ≈ **~15
req/game**, so **a 4-game day ≈ ~60 xG req** (concurrency stresses only the per-minute
cap, which is generous — not the daily quota). The free `/status` heartbeat adds zero
(doesn't count).

| Ceiling | 4-game day (~60 req) | Verdict |
|---|---|---|
| Free (100/day) | ~60, ~40 to spare | **Holds — but thin margin.** A 5th game, a double-poll bug, or added pre-match xG lookups breaches it. |
| Cheap paid (~7,500/day) | ~60 | Rounding error. Clears any WC day + UFC + slop. |

**Conclusion:** xG-only at 12-min ticks is *survivable* on free even on a 4-game day,
but the margin is thin enough that **subbing for the ~1-month WC window is the right
call** — it makes the budget boring instead of nail-biting. This validates the
original instinct (sub during WC, drop to free after) and shows *why*: it's margin,
not a hard wall.

**Design consequence — `can_spend` reserves a margin.** The budgeter must NOT spend to
zero. It keeps a small reserve (≈10–15 req) below `remaining`, so a tight free-tier
4-game day degrades the *last* game's xG gracefully (sparser ticks, then the loud
banner) instead of hard-429ing mid-match. The clamp protects the worst case, not just
the average one.

### LUTZ must internalize feed latency vs. the market (the actual confusion fix)

**Observed bug:** a goal is scored → odds spike → ESPN doesn't update for ~60s →
**LUTZ gets confused** — he sees the price moved but his feed still says 0-0, and
misreads the gap as a contradiction (or worse, as the market being wrong). This is
the single highest-value fix in this whole plan and it is *pure awareness*, not data.

LUTZ's grounding must state these as facts he reasons from:

- **"Your feed lags the live market by ~60s."** So his DEFAULT inference when price
  moves and his data hasn't caught up is *"an event just fired and my feed hasn't shown
  it yet"* — not confusion, not "the market is wrong." He should be able to say:
  *"Odds just spiked on [team] — my feed runs ~60s behind the book, so a goal likely
  just went in; confirming on the next poll."* The burst feature (`2026-06-03-002`)
  shrinks this window to ~10s but never to zero, so this awareness is required
  regardless.
  **Hedge — ~60s is approximate, calibrate during WC.** The figure came from
  stream-vs-market observation, but a broadcast stream has its own delay (often
  30–60s+ behind the stadium), so stream-vs-market and ESPN-feed-vs-market are not
  necessarily the same lag. LUTZ's grounding should phrase it as *"roughly ~60s,
  approximate — calibrate against a real goal during WC"* so he doesn't over-correct
  by stating a precise number that's actually his stream's delay, not his feed's.
- **The market is the fastest clock; ESPN is a ~60s-late confirmation; xG is slow
  context.** When they disagree, lag explains it far more often than mispricing does.
  He must NOT fade a move just because his feed hasn't caught up — that's lag, not value.
- **xG carries a game-time freshness stamp.** Every xG value LUTZ reads is tagged with
  the **match clock it was last updated at** (e.g. "xG as of 38'") alongside the
  current game time (e.g. "game at 47'"), so he knows it's ~9 game-minutes stale and
  weights it as context, not current. Game-time, not wall-clock — that's the frame he
  reasons in. (Implementation: `soccer_xg.py` records the fixture's match clock at
  fetch time; the snapshot LUTZ reads exposes `xg_as_of_minute` next to the value.)
- **xG is 10–15 min context, never a trigger.** A 12-min-old xG is fine for "this
  game's underlying balance," useless as a "react now" signal — he shouldn't conflate.
- When xG is paused (budget exhausted), LUTZ keeps reading on ESPN (the always-on
  floor) and *says* xG is paused rather than silently reasoning on stale/absent xG.

Exact surfacing of *budget* state (always-note vs. only-when-degraded) is decided when
the heartbeat is wired into the LUTZ skill in Phase 3. The *latency awareness* above is
not optional polish — it ships with the LUTZ grounding as the fix for the observed
confusion, independent of budget/heartbeat work.

### Starvation behavior (confirmed: degrade + surface loudly)

When a sport hits its ceiling mid-match:
- **Stop the metered polling for that sport** (further calls would just 429).
- **ESPN keeps running** — soccer score/clock/shots never go dark.
- **Surface loudly:** a banner — *"API-Sports soccer budget exhausted — xG paused,
  resets in Xh Ym (00:00 UTC). Sub to raise the ceiling."* Plus a persistent
  budget readout. Nothing fails silently. No auto-throttle in v1 (predictable
  cadence > squeezing the last few requests; revisit if it bites).

## Scope (this iteration)

**In:** Soccer xG + UFC live data. The per-sport budget/tier framework built to hold
all five sports, but only soccer + UFC actually wired.

**Out (scaffolded for later):** NFL, MLB, NBA data wiring. "Sub NFL in September"
becomes: set its tier intent + add the data mapping — no framework work.

## Keeping it clean as we go multi-sport (explicit guardrail)

The platform is not soccer-only forever — UFC and NFL are next, each with its own
portal and rules. But the way that breaks a codebase is **premature abstraction**:
building a grand generic `Sport`/`Portal`/`Rules` framework *now*, before we
understand UFC or NFL, so soccer pays a complexity tax for sports that aren't here.
That violates "no abstractions nobody asked for, no speculative flexibility."

So the seam is drawn deliberately:

- **Shared now (earned, not speculative):** the budget core (`client`, `budget`,
  `modes`, `status`). This is *genuinely* sport-agnostic because the API forces it —
  same auth header, same rate-limit headers, same per-sport quota model for every
  sport. The abstraction exists because reality already abstracted it for us.
- **Concrete now, NOT shared:** `soccer_xg.py` is soccer + xG, full stop. `ufc.py`
  is UFC, full stop. They are allowed to look nothing alike — because they *are*
  nothing alike (a cumulative match stat vs. round-by-round fight state). No shared
  "portal" interface between them in this iteration.
- **Extract the portal/rules abstraction AFTER UFC, never before.** One concrete
  sport tells you nothing about the shared shape; two (soccer + UFC) make the real
  commonality visible; NFL then validates it. Pulling a generic interface from a
  single example is guessing. We refactor to the shared shape when a second example
  reveals it — that's a planned, cheap refactor, not tech debt.

Net: multi-sport readiness lives entirely in the budget layer (where sport-agnosticism
is real and forced). The sport surfaces stay small, concrete, and independent until
the codebase has earned the right to unify them.

## Architecture

New module cluster under `ingestion/`, plus a small budget core. One small file per
responsibility (project style: many small modules).

```
backend/src/ingestion/api_sports/
  client.py        Thin httpx wrapper. Injects key header, reads the rate-limit
                   headers off EVERY response, hands them to the budgeter. Maps
                   429 / errors-body → a typed QuotaExhausted, never a raw raise.
  budget.py        Per-sport budget ledger. Holds {ceiling, remaining, resets_at,
                   plan_name} per sport, updated from response headers + /status
                   heartbeat. `can_spend(sport, margin)` → bool. Single source of
                   truth for "what's our budget right now." In-memory; rebuilt from
                   the next response after a restart (truthful within one call).
  modes.py         FREE | FULL per sport. Read from env at boot (intent). Maps
                   (mode, live?) → target cadence. Pure; no I/O.
  status.py        Polls /status per subscribed sport on a slow heartbeat (e.g.
                   30 min — free, doesn't count). Seeds plan_name + ceiling so the
                   UI shows subs even before the first data call of the day.
  soccer_xg.py     Soccer-specific: find live fixtures, pull xG, normalize to the
                   shape the existing soccer snapshot/matcher can merge. Reconciles
                   API-Sports fixture ↔ our game by (date, team-name) — the same
                   fuzzy trick the Kalshi↔ESPN matcher already uses.
  ufc.py           UFC live data normalize (new sport surface).
```

Surfacing:
- `api/routes/` — a `GET /api/feed/budget` returning per-sport
  `{plan, mode, ceiling, remaining, resets_at, exhausted}`. Read-only.
- WS broadcast on budget-state change (exhausted / recovered / sub detected) so the
  banner reacts without polling — consistent with the WS-context + `setQueryData`
  rule.
- `dashboard/` — a compact **Feed Budget** strip (Settings page + a slim header
  indicator). States: per sport, plan name + mode + a remaining/ceiling bar; amber
  when low, red when exhausted. Amber = action, never red for a mere warning
  (red is reserved for exhausted). Uses `font-mono tabular-nums` for the counts.

Config (`config.py`, extends the existing `Settings`):
- `api_sports_key` — single key (replaces the half-defined `api_football_key`;
  one account, all sports).
- `api_sports_soccer_mode` / `api_sports_ufc_mode` — `free | full`, default `free`.
  Per-sport mode intent. (Scaffold fields for nfl/mlb/nba, default `free`.)
- No tier/ceiling env — that's auto-detected, never declared. (This is the whole
  point of dial #1; declaring it would create a second source of truth that drifts.)

## Hard-rule compliance check

- **#2 single source of truth:** budget.py is the *only* home for live budget; ceiling
  is read from the wire, never declared in config. ESPN remains soccer's live feed;
  API-Sports adds xG, doesn't duplicate score/clock. ✅
- **#4 bind 127.0.0.1:** new route is read-only, same localhost server. ✅
- **#7 env not UI:** mode is an env var; no UI toggle that can be fat-fingered
  mid-match. The UI *shows* mode/budget, doesn't *set* it. ✅
- **#9 LLM abstraction:** N/A (no LLM calls here). ✅
- **Cross-market isolation:** this feed is read-only sports *data*; it touches no
  Kalshi positions/orders. ✅
- **Cents rule:** N/A — no money crosses this boundary (xG, fixtures, fight state). ✅
- **Money correctness:** feed data informs human decisions; it never sizes or places
  orders. A stale/missing xG degrades the read, never the ledger. ✅

## Steady-state check (a season in)

- **Budget:** auto-detected each call → can't drift. /status heartbeat is free →
  no quota cost to staying truthful. ✅
- **No unbounded growth:** budget ledger is a fixed per-sport dict (5 keys). No
  per-fixture accumulation that grows across a season. ✅
- **Mode is intent, ceiling is reality, clamp prevents starvation** → the "gets out
  of control" failure mode is structurally prevented, not just hoped against.
- **Drop-to-free after WC** = flip soccer mode env to `free` + restart. Ceiling
  auto-drops to 100 on the next call; xG goes sparse but ESPN keeps soccer fully
  live. No feed blackout. ✅

## Open questions / risks

- **[RISK] xG availability per competition.** API-Sports xG coverage is strong for
  top leagues; need to confirm it ships for *World Cup* fixtures specifically before
  WC. Mitigation: a 5-line probe against a past WC/major-tournament fixture id during
  build. If xG is absent for a competition, that game simply has no xG — ESPN data
  still stands (graceful degrade, same as a missing boxscore today).
- **[RISK] Fixture↔game reconciliation.** API-Sports team names won't always match
  ESPN's/Kalshi's exactly. Reuse and extend the existing normalize/alias approach;
  a miss means "no xG for this game," never a wrong-game xG (fail closed on ambiguity).
- **[ASSUMPTION] Cheapest paid tier (~$25–29/mo, ~7,500/day) clears a full WC slate.**
  4 games/day × ~30 xG req + UFC + overhead is well under 7,500. Confirm exact tier
  name/price/ceiling at sub time from the live pricing page (it 403s to automated
  fetch; check in a browser).
- **[OPINION] No auto-throttle in v1.** Predictable cadence + a loud banner beats an
  unpredictable budget-stretching algorithm. Add later only if real WC usage shows
  the loud-degrade is annoying in practice.

## Phasing

1. **Budget core** — `client.py` + `budget.py` + `modes.py`. Wire header-reading +
   `can_spend` clamp. Unit-test the budgeter against synthetic header sequences
   (incl. the 429/exhausted path). No data endpoints yet.
2. **/status heartbeat + budget route + UI strip.** Prove the "which subs do we have"
   surface works and is truthful, before any data flows. This is the
   "doesn't get out of control" guarantee made visible.
3. **Soccer xG** — `soccer_xg.py`, reconciliation, merge into the soccer snapshot the
   matcher/LUTZ already read. The WC payoff.
4. **UFC** — `ufc.py`, new sport surface end-to-end (validates the framework is
   genuinely multi-sport, not soccer-with-extra-steps).
5. **Scaffold NFL/MLB/NBA** mode fields + registry slots, no data wiring. Ready for
   September.

Phases 1–2 are the spine (truthful budget + loud surface); 3 is the WC value; 4
proves multi-sport; 5 is cheap future-proofing.
```
