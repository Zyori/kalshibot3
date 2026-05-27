/**
 * Live score + in-game stats line + last event narration.
 *
 * Three rows:
 *   1. Big score with team names: `Crystal Palace 1 - 0 Rayo Vallecano`
 *   2. Game-state pill (e.g. 75', HT, FT) + stats grid
 *      (shots, SOT, possession, cards, corners)
 *   3. One-line "Last:" narration of the most recent goal/card
 *
 * When `live` is null (pre-match or ESPN didn't match), we collapse to
 * just the team names + kickoff time. The caller passes a fallback title
 * for that case (event_title).
 */
import { formatET } from '../../lib/format'
import type { EventDetail } from '../../lib/types'

export default function MatchHeader({
  detail,
  decoded,
}: {
  detail: EventDetail | undefined
  decoded: string
}) {
  if (!detail) {
    return (
      <header className="rounded-lg border border-border bg-bg-card p-4">
        <h2 className="text-lg font-semibold text-text">{decoded}</h2>
      </header>
    )
  }
  const live = detail.live
  const kickoff = formatET(detail.open_time)
  const matchLabel = stateLabel(detail)
  const isLive = detail.espn_state === 'in'

  return (
    <header className="space-y-3 rounded-lg border border-border bg-bg-card p-4">
      {detail.league && (
        <div className="text-xs font-semibold uppercase tracking-wide text-action">
          {detail.league}
        </div>
      )}

      <ScoreLine
        homeName={live?.home_name ?? null}
        awayName={live?.away_name ?? null}
        homeScore={live?.home.score ?? null}
        awayScore={live?.away.score ?? null}
        fallbackTitle={detail.event_title ?? decoded}
      />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        {matchLabel && (
          <span
            className={`font-semibold ${
              isLive ? 'text-action' : 'text-text'
            }`}
          >
            {matchLabel}
          </span>
        )}
        {!isLive && kickoff && (
          <span className="text-text-muted">Kickoff {kickoff}</span>
        )}
        {live && <LiveStats live={live} />}
      </div>

      {live?.last_event && <LastEventLine event={live.last_event} live={live} />}
    </header>
  )
}

function ScoreLine({
  homeName,
  awayName,
  homeScore,
  awayScore,
  fallbackTitle,
}: {
  homeName: string | null
  awayName: string | null
  homeScore: number | null
  awayScore: number | null
  fallbackTitle: string
}) {
  if (!homeName || !awayName) {
    return <h2 className="text-lg font-semibold text-text">{fallbackTitle}</h2>
  }
  const showScores = homeScore !== null && awayScore !== null
  return (
    <div className="flex flex-wrap items-baseline gap-x-3">
      <span className="text-lg font-semibold text-text">{homeName}</span>
      {showScores ? (
        <span className="font-mono text-2xl font-bold tabular-nums text-text">
          {homeScore} - {awayScore}
        </span>
      ) : (
        <span className="text-text-muted">vs</span>
      )}
      <span className="text-lg font-semibold text-text">{awayName}</span>
    </div>
  )
}

function LiveStats({ live }: { live: NonNullable<EventDetail['live']> }) {
  const home = live.home
  const away = live.away
  const items: Array<{ label: string; home: string; away: string }> = []
  if (home.shots !== null || away.shots !== null) {
    items.push({
      label: 'Shots',
      home: String(home.shots ?? '—'),
      away: String(away.shots ?? '—'),
    })
  }
  if (home.shots_on_target !== null || away.shots_on_target !== null) {
    items.push({
      label: 'SOT',
      home: String(home.shots_on_target ?? '—'),
      away: String(away.shots_on_target ?? '—'),
    })
  }
  if (home.possession_pct !== null || away.possession_pct !== null) {
    items.push({
      label: 'Poss',
      home: home.possession_pct === null ? '—' : `${Math.round(home.possession_pct)}%`,
      away: away.possession_pct === null ? '—' : `${Math.round(away.possession_pct)}%`,
    })
  }
  if (home.corners !== null || away.corners !== null) {
    items.push({
      label: 'Cor',
      home: String(home.corners ?? '—'),
      away: String(away.corners ?? '—'),
    })
  }

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-text-muted">
      {items.map((it) => (
        <span key={it.label} className="font-mono tabular-nums">
          <span className="text-[10px] uppercase tracking-wide">{it.label}</span>{' '}
          <span className="text-text">{it.home}</span>
          <span className="px-0.5">-</span>
          <span className="text-text">{it.away}</span>
        </span>
      ))}
      {(home.yellow_cards > 0 ||
        away.yellow_cards > 0 ||
        home.red_cards > 0 ||
        away.red_cards > 0) && (
        <span className="font-mono tabular-nums">
          {(home.yellow_cards > 0 || away.yellow_cards > 0) && (
            <>
              <span className="text-[10px]">🟨</span>{' '}
              <span className="text-text">{home.yellow_cards}</span>
              <span className="px-0.5">-</span>
              <span className="text-text">{away.yellow_cards}</span>
            </>
          )}
          {(home.red_cards > 0 || away.red_cards > 0) && (
            <span className="ml-2">
              <span className="text-[10px]">🟥</span>{' '}
              <span className="text-text">{home.red_cards}</span>
              <span className="px-0.5">-</span>
              <span className="text-text">{away.red_cards}</span>
            </span>
          )}
        </span>
      )}
    </div>
  )
}

function LastEventLine({
  event,
  live,
}: {
  event: NonNullable<NonNullable<EventDetail['live']>['last_event']>
  live: NonNullable<EventDetail['live']>
}) {
  // Pick a tone: goals stand out in green, cards muted.
  const tone =
    event.kind === 'goal'
      ? 'text-gain'
      : event.kind === 'red'
      ? 'text-loss'
      : 'text-text-muted'
  const teamName =
    event.side === 'home' ? live.home_name : event.side === 'away' ? live.away_name : null
  const icon =
    event.kind === 'goal' ? '⚽' : event.kind === 'yellow' ? '🟨' : event.kind === 'red' ? '🟥' : '·'
  return (
    <div className="text-xs">
      <span className="text-text-muted">Last: </span>
      <span className={tone}>
        {event.minute && <span className="font-mono tabular-nums">{event.minute} </span>}
        <span>{icon} </span>
        <span>{event.text}</span>
        {event.player && <span> — {event.player}</span>}
        {teamName && <span className="text-text-muted"> ({teamName})</span>}
      </span>
    </div>
  )
}

function stateLabel(detail: EventDetail): string | null {
  if (!detail.espn_state) return null
  if (detail.espn_state === 'pre') return null
  if (detail.espn_state === 'post') return 'Final'
  // 'in' — espn_clock typically already carries the minute label
  return detail.espn_clock ?? detail.espn_status_detail ?? 'Live'
}
