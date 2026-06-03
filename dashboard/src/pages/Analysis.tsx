/**
 * Analysis — exit post-mortem surface. Placeholder.
 *
 * The backend now freezes the run-of-play (score, clock, shots, SOT, the
 * per-shot stream) at every entry and exit fill into trade_snapshot rows. This
 * page will read those back to answer "how have my exits been" — did we sell the
 * swing or ride past the peak, and were exits inside the 75-90' danger window.
 * Coming soon; the data is accumulating from each new trade now.
 */
export default function Analysis() {
  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold text-text">Analysis</h2>
        <span className="text-[11px] text-text-muted">Exit post-mortems · coming soon</span>
      </div>

      <section className="rounded-lg border border-border bg-bg-card">
        <div className="flex flex-col items-center gap-4 px-6 py-16 text-center">
          <span className="rounded-full border border-action/40 bg-action/10 px-3 py-1 text-[11px] font-medium text-action">
            Coming soon
          </span>
          <h3 className="text-base font-semibold text-text">
            Exit timing, from the game state at the fill
          </h3>
          <p className="max-w-md text-sm leading-relaxed text-text-muted">
            Every entry and exit now freezes the run-of-play at the fill moment —
            score, clock, shots on target, the per-shot stream. This page will line
            those up across closed trades to show whether exits caught the swing or
            rode past the peak, and which strategies leak P&amp;L in the 75–90′
            danger window.
          </p>
          <p className="max-w-md text-xs text-text-muted">
            Nothing to show yet — snapshots start accumulating from your next trade.
          </p>
        </div>
      </section>
    </div>
  )
}
