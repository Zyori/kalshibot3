# Post-audit multi-agent review — findings (2026-06-04)

A 9-reviewer multi-agent review of every commit since the 2026-05-30 money-path
audit (`git diff 424e956..HEAD`, ~60 commits, ~3,100 lines). This code had never
had a multi-agent look. Reviewers: correctness, adversarial, reliability,
data-integrity, project-standards, kieran-python, kieran-typescript, performance,
maintainability. Findings verified against actual code (reviewer line numbers ran
~3× inflated — code locations were real, citations were not).

## FIXED this session

- **Amend-vs-fill orphan race** (correctness + adversarial, convergent). The amend
  route's in-lock re-check only re-queried local `BetFill` rows. The WS fill handler
  that writes those rows is itself blocked on the ledger lock the amend holds, so a
  fill landing in the pre-lock window is invisible in-lock. Kalshi *accepts* amends
  on partially-filled orders ("max fillable = remaining + fill", per docs.kalshi.com),
  so the amend would succeed, issue a new order_id, and `reprice` would refuse the
  re-point — orphaning a live order with no bet tracking it. **Fix:** re-read Kalshi's
  `remaining_count` inside the lock, mirroring the pre-lock guard. Test:
  `test_amend_refused_when_kalshi_shows_fill_only_inside_lock`.
- **Duplicate cents converter** (project-standards, hard-rule-1). `orders.py`
  `_normalize_price_cents` re-implemented `dollars_str_to_cents`. Now routes the
  dollar-string branch through the canonical converter.
- **`avg_entry_price` duplicated** across `positions.py` + `events.py` →
  single-sourced to `core/types.position_avg_entry_price`. **The fee-INCLUSIVE
  formula was preserved** — a reviewer claimed Kalshi shows cost-only, but commit
  25fa868 verified the fee-inclusive value against kalshi.com (Arsenal 57.73 vs
  57.71). Do not drop the fees term.
- **Dead code:** dev-gated the `/analysis` placeholder route (was shipping to prod);
  removed the now-unused `ExitType` import in `orders.py`; removed the commented-out
  `KXWCGAME` dict entry in `soccer.py` (the explanatory prose comment stays).

## Open backlog (tracked, not fixed — low priority for the WC run)

**Reliability / steady-state (all bounded to a season; service restarts ~weekly):**
- `LiveState.books`, `Supervisor._last_tier`, `MarketRefresher._last_resync_at` grow
  monotonically — no eviction on DONE tier. Memory is bytes, not a crash risk.
- `STALENESS_TIMEOUT_S` (ws.py) defined + imported but no watchdog consumes it. The
  websockets `ping_interval=20/ping_timeout=20` default is the only silent-feed
  defense. Either wire a watchdog or delete the constant.

**Correctness (low sev, single-user):**
- Orphan-buy heal in `fills_sync` only recomputes the bet when `fee_cents` *changed*,
  so a single back-linked buy keeps requested price/stake/fees=0 until a restart
  re-derives. The other tail of the amend-race story.

**Performance (bounded at single-user scale — flagged as patterns, not bugs):**
- `_resting_order_from_kalshi` reads only the first 200 resting orders unpaginated;
  a cancel/amend could 404 behind 200+ resting orders (cross-market account).
- N+1 BetFill queries per bet in `settle_bets_for_market`; 2N queries per position in
  `_log_pnl_divergence`. Both bounded to a handful at single-user scale.
- `fetch_all_trades` re-pages Kalshi (up to 15 REST calls for a 3-market event page)
  on every chart open, no cache — could pressure the rate-limit bucket at WC volume.

**TS quality (cosmetic / type-drift):**
- `LiveSnapshot` missing `shots`; `market_status` union too narrow after the backend
  widened `Market.status` to `str`. Silent `any` on access.
- NO-side orders show the YES complement in the post-placement confirmation note
  (`OrderPanel`, `OpenOrdersCard`) — display-only, misleads at fill time.
- `nudge_evaluator` red-card trigger is bare `red_card` while the dedup key is
  `red_card:{count}` — a second red card is indistinguishable from the first.

## Deliberately NOT changed (reviewer findings overruled)

- `Suggestion.rejection_reason` + `suggestion_group_id` + `ix_suggestion_group`:
  flagged as dead code, but documented Phase-4 parlay reservations. Kept.
- `avg_entry_price` fee-inclusive formula (see above) — verified correct.
- The `suggestion` WS case using `invalidateQueries`: the rule is "never invalidate
  *hot* data"; suggestions aren't hot, and the code comments say so. Not a violation.
