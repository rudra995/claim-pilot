// Talks to the ClaimPilot API (backend/api/main.py). The dev server proxies /api and /ws to it.
import { useEffect, useRef, useState } from 'react'

async function request(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail : JSON.stringify(data?.detail ?? data)
    throw new Error(detail || `${response.status} ${response.statusText}`)
  }
  return data
}

export const api = {
  cases: () => request('GET', '/api/cases'),
  start: (caseId) => request('POST', '/api/cases/start', { case_id: caseId }),
  claims: () => request('GET', '/api/claims'),
  claim: (id) => request('GET', `/api/claims/${id}`),
  queue: () => request('GET', '/api/queue'),
  decide: (id, decision) => request('POST', `/api/claims/${id}/decision`, decision),
  addBill: (id, lines) => request('POST', `/api/claims/${id}/bill`, { lines }),
  discharge: (id) => request('POST', `/api/claims/${id}/discharge`),
  pay: (id, paid, reference) => request('POST', `/api/claims/${id}/payment`, { paid, reference }),
  patientNote: (id) => request('POST', `/api/claims/${id}/patient-note`),
}

// Streams audit events from /ws/trace as they are written. `claimId` narrows it to one claim;
// `after` is the last event already shown (-1 = only new ones). Reconnects if the socket drops.
export function useTrace({ claimId, after = -1, onEvent }) {
  const handler = useRef(onEvent)
  handler.current = onEvent
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    if (after === null) return undefined
    let socket
    let closed = false
    let last = after
    const open = () => {
      const params = new URLSearchParams({ after: String(last) })
      if (claimId) params.set('claim_id', claimId)
      const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
      socket = new WebSocket(`${scheme}://${window.location.host}/ws/trace?${params}`)
      socket.onopen = () => setConnected(true)
      socket.onmessage = (message) => {
        const data = JSON.parse(message.data)
        if (data.type !== 'event') return
        last = data.event_id
        handler.current(data)
      }
      socket.onclose = () => {
        setConnected(false)
        if (!closed) setTimeout(open, 1000)
      }
    }
    open()
    return () => {
      closed = true
      socket?.close()
    }
  }, [claimId, after])

  return connected
}

// Loads `load()` now and again whenever `refresh()` is called; calls close together are merged.
export function useLoad(load, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const timer = useRef(null)
  const loader = useRef(load)
  loader.current = load

  const run = () =>
    loader.current().then(
      (value) => {
        setData(value)
        setError(null)
      },
      (err) => setError(err.message),
    )

  useEffect(() => {
    run()
    return () => clearTimeout(timer.current)
  }, deps) // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = () => {
    clearTimeout(timer.current)
    timer.current = setTimeout(run, 250)
  }
  return { data, error, refresh, reload: run }
}
