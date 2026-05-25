export default function Ledger() {
  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-lg font-semibold text-text">Ledger</h2>
        <p className="mt-1 text-sm text-text-muted">
          Full bet history with tag filters. Built in Phase 3.
        </p>
      </header>

      <div className="rounded-lg border border-border bg-bg-card p-8 text-center">
        <p className="text-sm text-text-muted">No betting history yet.</p>
      </div>
    </div>
  )
}
