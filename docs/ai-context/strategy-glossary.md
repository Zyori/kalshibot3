# Strategy Glossary

What each tag means **in this user's head**, not the textbook definition.
The AI partner reads this file to ground prompts — keep it honest, keep it
short, fill in the blanks. Update as your thinking sharpens.

## Strategy

- **mean_reversion** — Betting against an early outlier swing while there's still enough time on the clock for the price to drift back toward fair. Tends to be held longer / to resolution.
- **mean_confirmation** — Riding a favorite that's leading early and confirming the opening line rather than fighting it.
- **lock_parlay** — Chain of high-confidence picks expected to all hit; modest combined return.
- **moon_parlay** — Chain of longshots, high-confidence underdogs, or one underdog paired with locks for added value.
- **underdog** — Single underdog you think is mispriced — value the market isn't seeing.
- **predictive** — Pre-event entry on an open line that looks undervalued vs. your read, before the game starts.
- **time_decay** — Holding a position (often a draw) that gains value purely from the clock running down with the game state unchanged.
- **scalp** — Opportunistic quick trade for a smaller, faster return — often news-driven (red card, injury, momentum swing). In and out, not held to resolution.
- **hedge** — Closing risk on an open position by taking the other side; common when one leg of a parlay is left and the locked value beats the variance.
- **manual** — Catch-all when none of the above fits; describe the real shape in the memo.

## Source

- **human** — you placed it from your own read.
- **ai** — the AI partner proposed it and you accepted as-is.
- **collaborative** — you and the AI talked it through; the bet reflects both.
- **external** — placed on kalshi.com directly, reconciled here for the record.

## Timing

- **pre_match** — placed before kickoff / opening whistle.
- **live** — placed while the game is in progress.
- **futures** — tournament-level / season-level / multi-game horizon.

## Confidence

Pure gut, expressed as sizing intent. Floor is already "I'd bet this" —
if conviction is below that, no bet gets placed, so there's no `none`
bucket. The three values describe **degrees of yes**, anchored to a
rough multiple of your standard unit.

Record what you *felt at order time*. **Do not retag confidence after a
bet resolves** — it poisons the calibration signal the AI uses to learn
when your gut is sharp.

- **high** — Strong conviction. Sized around 2× unit. The "I'm sure" pick.
- **medium** — Standard play. Sized at 1 unit. Default bucket — most bets land here.
- **low** — Borderline-pass, but enough edge to take a swing. Sized around 0.5× unit.

## Tags (free-form)

Tags are free-form strings you add per-bet. No fixed list. As patterns
emerge ("tilted-after-loss", "model-disagreement", "thin-market", etc.),
they become AI prompt context. Don't constrain them — long-tail signal is
the point.
