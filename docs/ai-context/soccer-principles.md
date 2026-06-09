---
title: "Soccer Trading Principles"
type: strategy-knowledge-base
status: active
scope: Kalshi live soccer. Sits on top of global-principles.md (read that first).
note: Game-state setups + soccer-specific stats. World Cup 2026 notes inline.
---

# Soccer Trading Principles

Read `global-principles.md` first — this adds soccer-specific game-state reads on top.

## The three core setups

**1. Underdog scores early → mean reversion (DRAW or FAVORITE, by favorite strength).**
The early underdog goal is an outlier the favorite usually corrects — but *what* you buy depends on
how strong the favorite is. Read it off the favorite's **pre-goal moneyline**:
- **Heavy favorite (≥65¢ pre-goal) → back the FAVORITE to win.** A genuinely dominant side concedes
  early and still wins outright more often than it merely draws — so buy the favorite, not the draw.
  Stronger the earlier the goal and the more it's chippy/lucky (against the run of play).
- **Smaller favorite (<65¢ pre-goal) → back the DRAW.** Here the favorite usually claws ONE goal
  back, which swings the draw price up. Buy the draw cheap (~20–28¢), target +50–75%, and sell into
  the equalizer spike — almost never hold to settle.
- Either way: stronger if the underdog's goal was lucky/chippy AND the favorite is getting **shots,
  especially shots on target.**
- Skip if the favorite is toothless (no shots on target, generating nothing) — no comeback comes,
  for the win *or* the draw.

**2. Favorite scores early AND is playing well → consider the FAVORITE (mean confirmation).**
Take the favorite if there's value left, especially if they're performing strongly. The first
goal confirms them (first goal → ~70% no-defeat).
- **Hold this one longer** — until the tide turns, signalled by the other side showing **shots on
  target or corner accumulation.** Those are your exit triggers.

**3. Dead-state game → consider DRAW / UNDER (time decay).**
20–25 min of passive play and strong defense. 0-0 is an unlikely *final* outcome, but there's
draw/under value to hold and sell before the game ends. Options: Draw, Under 0.5, Under 1.5.
- Harder to read — teams can be explosive. Treat as lower conviction; ride the drift, sell early.

**4. Early goal panic-dumps the UNDER → buy the floor, scalp the bounce (scalp).**
An early goal hammers Under 1.5 to the bottom of the book as the market overreacts to one event. The
edge is **the overreaction, not the game going quiet** — an early goal actually argues for *more*
goals (the game opens up; see line 76), so you are NOT betting it stays low. You're buying a
mispriced floor and selling the bounce.
- Buy Under 1.5 when it's dumped to **≤7¢** — great value at 7¢ or less; the asymmetry is the point.
- This is a **scalp: in and out.** Target a quick double-up off the bounce, exit, don't marry it.
  Do not hold it as a thesis that the game finishes under — the run of play is against you, so the
  exit discipline carries the whole trade.

## Reading the run of play

- **Real threat = shots on target, big chances, shots from inside the box, posts, dangerous
  penalties.** Goals come from inside the box; this is what predicts the next goal.
- **NOT threat:** possession %, hopeful long-range shots (statistically near-worthless). A team
  with high possession and no shots on target is being contained, not breaking through —
  possession is a trap signal.
- **Corners = a dominance/territory signal, NOT a scoring chance** (~2–3% convert directly). Many
  corners means a team is on top, but it does not mean a goal is coming.
- **No "overdue" goal.** Missed chances don't make the next more likely. Bet who's creating NOW.
- **Pressure alone is not a thesis-break.** A team pushing (possession, territory, urgency) does
  not break your draw/under/lead thesis. Only shots on target / big chances do. Don't panic-exit
  on pressure; require real-chance evidence.
- **Watch goalkeeper quality.** Soft goals are a pattern, not luck — a bad keeper ships them all
  match. A leaky keeper makes Over / the opponent's goals more likely and undermines draw/under
  theses. Re-rate the whole match after one soft goal.
- *(xG would be the cleanest threat metric — chance quality, not just on/off target — but we
  read off basic stats: SOT, big chances, shot location.)*

## Key stats / params

- **Two goal-dense danger windows: 40–45' and 75–90'+.** Defenders lose focus before half, and
  the final stretch is the highest-density of all. Don't get caught holding a fragile draw / under
  / lead into these windows.
- **The clock rules below are priors for *fragile* positions, not laws.** "75' close-out", "sell
  the late 1-1" — these assume a position the danger window can break (a thin draw, a one-goal lead
  the other side is pressing). A position the game state is actively *confirming* (a lead that's
  firming, the other side generating nothing) can be **held through** these defaults. The clock
  tells you where the danger window is; the live read tells you whether *this* position is fragile
  or firming. **EV decides — the clock just raises the bar to keep holding.**
- **75' (±5 min) is the default close-out point.** Goal density peaks after 75', so bank
  draw/under/lead positions around then — UNLESS the game state strongly favors you (e.g. you
  backed a favorite at 1-0 and they're 2-0 by 75', or 1-0 with the other side toothless), in which
  case hold toward resolution.
- **First goal → ~70% no-defeat** for the scoring team. Grounds setups 1 and 2; once a side leads,
  the trailing team must open up, creating the counter-attack space behind the late-goal trend.

## Game-state probability rules of thumb (directional, not exact)

- 0-0 at 70–75' → draw becomes the likely single outcome (~55–65%); under stays strong.
- Leader up 1 at 75' → wins roughly 75–80%; held leads firm up fast late.
- Leader up 2 by 75' → near lock; hold to resolution rather than trade.
- 1-1 entering the last 15' → draw is live but the danger window threatens it; lean to sell, not hold.
- Early goal (before ~35') → expect more goals; the game opens up, favors Over and the trailing side.

## A goal against your thesis is a re-price, not an auto-exit

Backed a 1-0 favorite (setup 2) and it's 1-1 with 30+ min left? Your *entry reason* is gone, but
they're often still the stronger side and still favored to win — **re-rate from the new state and
price the win, don't just dump the swing.** Selling here converts a live, still-+EV position into a
locked loss and kills every path back. The sell is right when they're **no longer favored**, or the
**clock's too short to recover** (last ~10–15', stoppage) — not the moment the equalizer lands.

The deliberate exception is **setup-1 draws**: a goal there is the *equalizer spike you bought for*,
and you sell into it (line 17). The difference is whether the goal **confirms** your position (draw
play → equalizer = your exit) or **dents** it (lead/confirmation play → equalizer = re-price, often
a hold). Know which one you're in before you touch the sell.

## World Cup 2026 notes

- **48 teams, 12 groups, 24 of 32 advance** (top 2 + 8 best third-placed). You can finish 3rd and
  go through — this distorts incentives.
- **Final group games manufacture dead-state games:** teams often need only a draw to advance, so
  both play safe → strong setup-3 (draw/under) territory. Read the live qualification math.
- **More mismatches** (weak qualifiers vs elites) → more early favorites and lopsided games.
- **Dead rubbers** (already qualified/eliminated) → rested lineups, low intensity → favor under/draw.
- **Knockouts:** both must win or go to extra time → suppresses early draw value; level games late
  raise the 90-min draw.
