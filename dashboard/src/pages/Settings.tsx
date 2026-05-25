export default function Settings() {
  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-lg font-semibold text-text">Settings</h2>
        <p className="mt-1 text-sm text-text-muted">
          Bankroll, API status, preferences. Wired up alongside config endpoints in Phase 1.
        </p>
      </header>

      <div className="rounded-lg border border-border bg-bg-card p-4 text-sm text-text-muted">
        Settings UI lands once the backend exposes config + API status endpoints.
      </div>
    </div>
  )
}
