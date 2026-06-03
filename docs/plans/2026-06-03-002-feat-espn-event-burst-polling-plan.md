# ESPN event-burst polling — fast feed when the market moves

**Status:** planned — no code yet
**Date:** 2026-06-03
**Owner:** Zyori

## Problem

ESPN polls at 40s baseline when a game is live. That's fine for the slow ~95% of a
match. But when an event fires (goal, red card, penalty), the **market reprices in
seconds** while ESPN's feed — and therefore LUTZ — is still up to 40s behind on
*what actually happened*. We want LUTZ to learn the detail (which event, the
follow-on /summary shots/commentary) faster during those bursts, without
fast-polling all game.

## Honest framing — this is polish; the real fix is LUTZ awareness

The observed problem is: a goal is scored → odds spike → ESPN doesn't update for
~60s → **LUTZ gets confused** (sees price moved + ESPN still 0-0, misreads it as a
contradiction). The *primary* fix for that is NOT faster polling — it's teaching LUTZ
his feed lags the market by ~60s so he infers "an event just fired" instead of getting
confused. That awareness fix lives in the API-Sports/feed plan + the LUTZ skill
grounding (see `2026-06-03-001`'s "LUTZ must internalize feed latency" section).

This burst feature is **secondary polish**: it shrinks the confused window from ~60s
to ~10s. It does NOT beat the market to the news (the spike already happened when we
detect it) and it never drives lag to zero — so the awareness fix is required
regardless of whether we ship the burst. Build the burst knowing it's the speed bonus,
not the cure.

## Confirmed facts

- **ESPN's free `site.api.espn.com` scoreboard/summary has no published rate limit**;
  polling every few seconds is common and not throttled in practice. A 10s burst for
  ~75s is nowhere near risky. The self-discipline of bursting-only-on-events is for
  cleanliness and future-proofing, not because ESPN forces it.
- The existing poller (`ingestion/espn_scoreboard.py`) already computes a single
  global `interval` per cycle (40s live / 30min idle). This feature makes cadence
  **per-game and event-aware** instead of one global number.

## Design (deliberately small)

**Trigger — market spike (per game):** when the Kalshi market for a live game moves
sharply (mid-price jump ≥ N¢, or the spread blows out) within a short window, flip
*that game* into burst mode. The WS book data already flows through the app; the
trigger reads it — no new feed.

Spike threshold is **wide — a ≥10¢ mid-price jump** (not 3¢; normal book churn is
noise, only a real event moves the mid double digits). This is mostly polish, so the
trigger should fire only on the unambiguous "something big just happened" moves.

**Burst — 10s cadence for ~75s, then decay to 40s.** ~7–8 extra polls per event.
Grabs the /summary detail (shots, commentary, the event label) quickly.

**Guards (the "don't rate-limit us / don't run away" instinct, made concrete):**
- **Per-game, not global.** Only the game whose market spiked bursts. Caps blast
  radius on a 4-game WC slate — three quiet games stay at 40s.
- **Cooldown (~60s).** After a burst window ends, that game can't re-burst for ~60s.
  Stops a thrashing market from holding a game in permanent 10s polling — this is the
  real guard against runaway request volume, more than the rate limit ever is.
- **Hard floor 10s.** Burst never polls tighter than 10s, even if events keep firing.
  One ceiling knob, can't run away.

That's the whole feature: make `interval` per-game, add a per-game `burst_until`
timestamp set by the market-spike trigger, honor cooldown + floor. ~40 lines on the
existing cadence logic, no new module strictly required (may live in
`espn_scoreboard.py` or a small `burst.py` helper for the trigger threshold logic).

## Why NOT bump the baseline to 30s instead

A flat 30s baseline costs 33% more requests *all game* for the 95% dead time, and
buys ~10s on average where 10s is meaningless. The burst gives the speed exactly when
it matters and costs nothing the rest of the time. Baseline-bump is paying
continuously for an occasional benefit — rejected. Baseline stays 40s.

## How this relates to the market spike that triggers it

This reuses the same market-move signal family as the sanity-guard tiers
([[feedback_sanity_guard_rules]]) — a sharp book move. Different consumer (here it
speeds the feed; there it warns on bad-value orders), same underlying "the market
just moved hard" detector. Worth checking at build time whether that detection can be
shared rather than duplicated (single-source-of-truth).

## Open questions

- **[ASSUMPTION] Burst targets /summary, not just /scoreboard.** The detail LUTZ wants
  (which event, shot stream) is in /summary. Burst should prioritize the /summary
  fetch for the spiking game. Confirm the poller can burst one game's /summary
  independently of the scoreboard sweep.
- **[RISK] Pre-match / no-market games.** A game with no live Kalshi market (or one
  LUTZ isn't watching) has no spike signal — it just stays at 40s. That's correct
  (no market = no one trading it = no urgency), but worth noting the burst only
  helps games with an active market.

## Relationship to the API-Sports plan

Fully independent. This is a pure ESPN-poller enhancement driven by market data, no
API-Sports dependency. Note: `2026-06-03-001` (the API-Sports soccer-xG plan) was
**superseded on 2026-06-03** — a live probe proved API-Sports carries no xG for World
Cup fixtures (it's a per-competition gap, not a tier/timing issue; EPL has xG, WC
doesn't). This burst plan stands on its own and is unaffected — it was never about xG.
The LUTZ feed-latency awareness fix referenced above lives in the LUTZ skill grounding,
also independent of API-Sports.
