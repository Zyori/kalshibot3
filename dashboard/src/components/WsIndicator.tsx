import { useWebSocketStatus } from '../contexts/WebSocketProvider'

/**
 * Tiny dot in the header that reflects the live-data connection.
 *   green   connected
 *   amber   connecting / reconnecting
 *   red     closed (will be reconnecting unless the app shut down)
 *
 * Title attribute spells out the state for accessibility and the curious.
 */
export default function WsIndicator() {
  const status = useWebSocketStatus()
  const tone =
    status === 'open' ? 'bg-gain'
    : status === 'connecting' ? 'bg-action'
    : 'bg-loss'
  const label =
    status === 'open' ? 'Live data connected'
    : status === 'connecting' ? 'Connecting to live data…'
    : 'Live data disconnected (reconnecting)'

  return (
    <span
      title={label}
      aria-label={label}
      className={`inline-block h-2 w-2 rounded-full ${tone}`}
    />
  )
}
