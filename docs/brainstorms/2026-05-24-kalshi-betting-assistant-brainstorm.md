---
date: 2026-05-24
topic: kalshi-betting-assistant
---

# Kalshi Betting Assistant — Interactive Dashboard & AI Copilot

## What We're Building

A sports betting workbook with an AI brain attached.

The app is organized into **sport portals** — like folders in a filing cabinet. Each sport follows the same template: news, AI-suggested bets, open positions, live markets, and a historical ledger. Same structure, different content. Adding a new sport means filling a new folder, not rebuilding the app.

**Trading is built in** because we already have the Kalshi socket open for live data. There's no reason to tab to Kalshi's site when we can see markets, enter sizing (with Kelly recommendations pre-filled), and click to execute — all from the same interface that shows our analysis. V1 already proved this works with position cards and close buttons.

**The AI is a proactive betting partner.** It does its own homework — scans news, injuries, odds movements, off-field drama — and surfaces specific, justified bet suggestions on the dashboard before you even ask. When you open the app in the morning, there's already a list waiting: "Here are 3 bets I think we should place, here's why, here's the value." You either agree, counter-propose your own ideas, or open a discussion. It's a two-way street: the AI suggests bets, you suggest bets, and together you choose which ones actually get placed. The chat panel is where the discussion and reconciliation happens. Every decision — what you placed and why — gets logged.

**The ledger is permanent.** Every bet, every reason, every outcome. This builds into a personal betting analytics platform — data visualization, trend identification, strategy performance tracking across sports and time.

**Target launch: World Cup 2026 (June 14, 2026) — ~3 weeks from brainstorm.**

## Core Thesis

The app has value even if the AI is dumb. Three layers, each independently useful:

1. **Betting workstation** — Replace Kalshi's interface. See markets, place bets, manage positions. Better frontend, faster execution. Value on day one with zero AI.
2. **Logbook** — Every bet logged with who proposed it (human or AI), the reasoning, the conviction weight, whether there was disagreement, and the outcome. Over time this becomes a dataset for pattern recognition: where you burn money, where your instincts are sharp, where you override AI and win (or lose). This has value even if AI suggestions are a coin flip.
3. **AI partner** — Proactive suggestions, catching bad habits ("you always oversize on draws"), Kelly discipline enforcement, fresh perspective, predictive analysis. This is the ceiling — if it works, it multiplies your edge. If it doesn't, layers 1 and 2 are still the most useful betting tool you've ever had.

The best outcome combines human emotional intuition with AI data crunching. But the worst outcome is still a polished workstation with a rich decision log. There is no scenario where this app is useless.

## Why This Approach

### Approaches Considered

1. **Monolith Dashboard (chosen)** — Single FastAPI backend + React frontend. Fastest to build, reuses V2 patterns, ships in 3 weeks.
2. **Microservices** — Separate data/analysis/trading services. Over-engineered for single-user tool, won't ship in time.
3. **Chat-centric (LLM as orchestrator)** — Natural but expensive, fragile, and inverts the "scripts monitor, LLM analyzes" principle.

### Why Monolith

- 3-week deadline demands simplicity
- Single-user tool doesn't need distributed architecture
- V2 codebase (FastAPI, SQLAlchemy, Kalshi auth) is directly reusable
- Can extract services later if genuinely needed

## Key Decisions

### Architecture
- **Monolith**: Python/FastAPI backend, React frontend, SQLite database
- **Sport wrapper pattern**: Each sport is a "bucket" — a container with universal sections (news, strategy, learnings, ledger, suggested bets, history) filled with sport-specific content. The wrapper defines the structure; the sport defines the substance.
- **Universal core**: Dashboard shell, chat, Kalshi integration, position management, Kelly sizing, alerts — all sport-agnostic
- **Sport-specific modules**: Event types, scoring patterns, market mappings, strategy logic, data source adapters — per-sport
- **Memory per sport**: Separate ledger/journal/learnings files for each sport bucket

### Compute Strategy
- **Scripts/WebSockets monitor**: Cheap, always-on processes watch live scores, odds changes, game events
- **LLM analyzes at decision points**: When scripts detect interesting events (score change, odds movement, injury), escalate to LLM for analysis
- **Tiered models**: Fast/cheap model (Haiku, GPT-4o-mini) for event classification; smart model (Opus, GPT-4o) for strategy discussion and bet justification
- **NOT continuous LLM inference**: LLM is called at key moments, not polling every second

### Trading & Position Management
- **One-click buy/sell** through our interface via Kalshi API (WebSocket for speed)
- **Kelly-based sizing**: Quarter-Kelly or less given ~$500 bankroll. Every suggestion includes specific size and price.
- **Live position monitoring**: App tracks open positions against live game state
- **Hedge/close suggestions**: AI monitors multi-leg positions and suggests when to lock in profit or hedge risk
- **Set bids and sell thresholds**: Full order management, not just market orders

### Betting Strategy (Four Lenses)
1. **Mean reversion / mean confirmation (core)**: Exploiting market overreactions to game events. Sport-specific — see per-sport strategy sections below.
2. **"Money maker" lock parlays**: 2-4 leg parlays built from high-confidence picks. Amplified returns when we have edge on multiple games.
3. **Underdog/upset identification**: 1-2 value underdog picks per event window. Contrarian plays against public consensus. Teams that are close-but-unlucky or due for regression.
4. **Longshot "moon" parlays**: 3-7 legs mixing a few locks with 1-2 reasonable longshots. At least 1 per week. Amplified edge — if we have edge on individual legs, parlays leverage it.

#### Soccer Strategy (World Cup — PRIMARY)
- **Draw value**: Draws happen ~25% of group stage matches but public hates betting draws. Potential consistent edge on draw markets.
- **Mean confirmation over mean reversion**: Soccer is low-scoring (most games 0-3 goals). An early goal often *confirms* the favorite rather than creating reversion. The question is: when does an early goal create real value vs. trap value?
- **Red card impact**: A red card at minute 30 vs. minute 75 has wildly different expected impact. Market may overprice or underprice depending on timing.
- **Expected goals (xG) vs. actual**: Teams that are outperforming xG are due for regression. Teams underperforming xG are undervalued.
- **Group stage dynamics**: Dead rubbers (teams already qualified/eliminated) create bizarre incentives. Motivation mismatches = value.
- **Home continent advantage**: 2026 is USA/Mexico/Canada — North American teams and those with large diaspora fanbases will have pseudo-home advantage.
- **Tournament fatigue**: Teams playing every 3-4 days. Deep-run teams in knockout stages accumulate fatigue. Travel between host cities matters.
- **Kalshi market types**: Match results, tournament winner, furthest stage, group goals, and more. Strategy should adapt to whichever markets offer the best liquidity and edge.

#### NFL Strategy (FUTURE — for reference)
- Luck-based events: returned kickoffs/punts, missed 3rd downs, field goals instead of touchdowns
- Backup QB insertion: sometimes overfaded (hungry backup like Nick Foles), sometimes safest fade (green QB)
- Scheduling/travel: Thursday night games, West Coast teams playing early East Coast games
- Weather impact on passing games
- Record-based regression: 3-0 with close/lucky wins = overvalued, 0-3 with close losses = undervalued
- Sunday 1pm + 4:30pm EST = maximum game overlap = maximum AI value

### Universal Patterns (Apply to All Sports)
- Popular teams attract more volume (people bet their favorites) — fade the public when consensus is too strong
- Invert consensus when "everyone agrees" — best value is fading "safe" bets
- Sense whether we're early to a trend or riding public consensus — early = good, riding = dangerous
- No star performs well every game — look for big moments to ride against expectations
- Differentiate "hidden value the midcurvers are fading" from "public consensus we should invert"
- Over is often overvalued (public loves scoring) — especially in matchups where it's "obvious"
- Record-based regression: winning streaks with close/lucky wins = overvalued, losing streaks with close games = undervalued

### AI Behavior & Workflow

**Agent states/phases:**
1. **Research**: Scans news, injuries, weather, lineups, scheduling, travel throughout the week
2. **Analyze**: Processes data into actionable insights, identifies opportunities
3. **Suggest**: Proposes specific bets with confidence interval, Kelly sizing, and justification
4. **Justify**: Explains reasoning — what factors, what historical patterns, what edge

**User states:**
5. **Propose**: User suggests emotional/intuitive bets
6. **AI analyzes user proposal**: Validates or pushes back with data
7. **Discuss and finalize**: Collaborative decision

**Live game behavior:**
- Most active during overlapping game windows (e.g., Sunday 1pm EST for NFL)
- Actively scanning, thinking, suggesting during live games
- Time-sensitive alerts with sound for urgent opportunities
- Monitors open positions and suggests hedges/closes

**Example suggestions:**
- "Ravens and Chiefs ML both moved because they were scored on. Consider a 2-way parlay at 1/3 unit. VERY time sensitive."
- "Giants down 4 at half but should be winning — two TDs turned to FGs on bad calls. Take Giants 2nd half ML, 1/5 unit."
- "Steelers putting in backup QB. Game is over. Packers ML, 2 units."
- "You've hit 2/3 legs on this parlay. Consider hedging the last leg."

### UI/UX Design Principles
- **Dashboard is the product, chat is a companion**: The dashboard has dedicated sections for each function — suggested bets, open positions, news/research, history/ledger, alerts. Each section is self-contained and functional. The chat panel is a copilot for discussing and thinking, not the primary interface for doing things. The app should work fully without the chat.
- **State-driven layout**: Looks different when games are live vs. not. Open positions drive focus.
- **Embedded chat panel**: Collapsible sidebar on the right. Can discuss any element on the dashboard ("discuss this bet" links to chat). The mind behind the system, not the control surface.
- **Visual, formatted, well-labeled**: Not a dev tool. Clean visual hierarchy for use under pressure during live games.
- **In-app alerts with sound**: Visual highlights + audio ping for urgent suggestions. Alerts surface in their own dedicated area, not buried in chat.
- **Scannable data**: Recent news, strategy/analysis, suggested bets, history/summary — all visible in dedicated sections without drilling down.
- **Polished and designed**: Feels like a real product, not a dev tool or data dump. Clean typography, intentional spacing, good use of color for status/urgency. Closer to V1 dashboard quality. Simplicity in navigation and data organization despite showing a lot of data.
- **Per-sport sections**: Each sport is a segregated section/page within the broader app.

### Data Sources
- **Kalshi API**: Markets, orderbooks, positions, order management (REST + WebSocket)
- **API-Football ($19-39/mo)**: Live scores, match events (goals, cards, subs, corners), lineups, injuries, form, H2H. 15-second polling latency — acceptable for human-in-the-loop decisions. World Cup coverage confirmed. Pro tier ($19/mo) sufficient if we keep The Odds API for odds; Ultra ($39/mo) if we want their odds data too.
- **The Odds API** (existing from V2): Odds from multiple books for line comparison. Does NOT provide match events — only scores and odds. Keep for odds, pair with API-Football for match data.

### Memory & Logging (Full Journal + Historical Analytics)
- **Bet ledger**: Every bet logged with: who proposed it (human, AI, or both), the reasoning from each side, whether there was disagreement, the final consensus, sizing rationale, and outcome
- **Rich tagging system** for filtering and analysis. Every bet gets tagged across multiple dimensions:
  - *Source*: human-proposed, AI-proposed, collaborative
  - *Strategy*: mean reversion, mean confirmation, lock parlay, moon parlay, underdog, draw value, live event, etc.
  - *Lifecycle*: closed early, held to settlement, hedged, partial close
  - *Timing*: pre-match, live (with minute/phase), futures
  - *Conviction*: high/medium/low (from whoever proposed it)
  - *Outcome*: win, loss, push, voided
  - *Override*: did human override AI sizing? Did human override AI suggestion? Did AI talk human out of a bet?
  - *Sport + event*: World Cup Group A, NFL Week 3, etc.
  - All filterable and cross-referenceable for trend analysis
- **Pattern recognition on YOU**: AI analyzes your betting history to catch bad habits — "you're 2-8 when you override sizing recommendations," "your draw instincts hit 60%," "you oversize on longshots after a winning streak"
- **AI post-mortems**: AI reviews wins and losses, its own suggestions and yours
- **Per-sport separation**: Each sport has its own memory/journal files
- **Data-driven feedback loop**: Track which strategies are actually profitable over time
- **Weekly/monthly summaries**: Aggregated performance reviews
- **Long-term performance analytics**: The app doubles as a personal betting history platform. Cross-sport P&L, ROI by strategy type, win rate trends, bankroll growth over time. This is the historical record that doesn't exist today — years of unlogged betting finally get tracked.
- **Manual entry for past bets**: Ability to log historical bets that happened before the app existed, so the ledger can eventually represent the full picture

### Bankroll
- Starting bankroll: ~$500 or less
- Position sizing: Quarter-Kelly or less (conservative for small bankroll)
- Unit size: Configurable, likely $5-25 range
- Every suggestion includes specific dollar amount and price

### What This Is NOT
- NOT a fully automated trading bot (learned from V1 and V2)
- NOT an LLM-first architecture (scripts monitor, LLM analyzes)
- NOT a soccer-only app (soccer first, sport-agnostic architecture)
- NOT a replacement for Kalshi's interface (it's a superset — everything Kalshi offers plus AI)

## Open Questions

*All resolved during brainstorm — none remaining.*

## Resolved Questions

- **Kalshi World Cup markets**: Games (match results), futures (tournament winner), furthest stage, group goals, and more. Confirmed available.
- **Soccer strategy**: Start with draw value + xG-based analysis. Refine live strategy through World Cup group stage experience. Learn as we go.
- **Timeline**: World Cup 2026 (June 14) is hard deadline (~3 weeks)
- **MVP scope**: Full dashboard + chat + one-click trading for soccer
- **Architecture**: Monolith (FastAPI + React)
- **LLM strategy**: Scripts monitor, LLM analyzes at key moments. Tiered models.
- **Alert system**: In-app visual + sound only for MVP
- **Bankroll**: ~$500, quarter-Kelly sizing
- **Memory**: Full journal with AI post-mortems
- **Markets**: Match outcomes + tournament futures + selective live props
- **Chat UX**: Embedded panel, always visible alongside dashboard
- **UI feel**: Visual, formatted, well-labeled, state-driven, polished — feels like a real designed app
- **Framework**: Sport-agnostic core, sport-specific plugins. Soccer first, NFL second.
- **One-click trading**: Yes, full order management through our UI

## Next Steps

-> `/ce:plan` for implementation details — file structure, component breakdown, API design, build order for 3-week sprint.
