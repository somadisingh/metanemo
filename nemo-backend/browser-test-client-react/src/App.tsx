import { useEffect, useRef, useState } from 'react'
import './App.css'

type ChatRole = 'user' | 'assistant' | 'sys'
type ChatMessage = { role: ChatRole; text: string }

function App() {
  const [wsUrl, setWsUrl] = useState('ws://localhost:8080/ws')
  const [nemoclawBase, setNemoclawBase] = useState('http://localhost:8090')
  const [lat, setLat] = useState('40.7580')
  const [lon, setLon] = useState('-73.9855')
  const [messageInput, setMessageInput] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [apiOut, setApiOut] = useState('')
  const [wsConnected, setWsConnected] = useState(false)
  const [locating, setLocating] = useState(false)
  const [locState, setLocState] = useState('Location source: default coordinates')
  const [endpointPath, setEndpointPath] = useState('/v1/agent')
  const wsRef = useRef<WebSocket | null>(null)

  const addMsg = (role: ChatRole, text: string) => {
    setMessages((prev) => [...prev, { role, text }])
  }

  const coords = () => ({
    latitude: Number(lat),
    longitude: Number(lon),
  })

  const sendJson = (payload: unknown): boolean => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      addMsg('sys', 'WebSocket not connected.')
      return false
    }
    ws.send(JSON.stringify(payload))
    return true
  }

  const requestCurrentLocation = async (silent = false): Promise<boolean> => {
    if (locating) return false
    if (!('geolocation' in navigator)) {
      setLocState('Location source: browser geolocation unavailable')
      if (!silent) addMsg('sys', 'Browser geolocation is unavailable.')
      return false
    }

    setLocating(true)
    return new Promise((resolve) => {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          const nLat = Number(position.coords.latitude.toFixed(6))
          const nLon = Number(position.coords.longitude.toFixed(6))
          setLat(String(nLat))
          setLon(String(nLon))
          setLocState(`Location source: browser geolocation (${nLat}, ${nLon})`)
          if (!silent) addMsg('sys', `Using current location: ${nLat}, ${nLon}`)
          const ws = wsRef.current
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ realtimeInput: { location: { latitude: nLat, longitude: nLon } } }))
          }
          setLocating(false)
          resolve(true)
        },
        (error) => {
          setLocState(`Location source: default coordinates (${error.message})`)
          if (!silent) addMsg('sys', `Could not get current location: ${error.message}`)
          setLocating(false)
          resolve(false)
        },
        { enableHighAccuracy: false, timeout: 2500, maximumAge: 300000 }
      )
    })
  }

  const connect = () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setWsConnected(true)
      addMsg('sys', `Connected to ${wsUrl}`)
      ws.send(JSON.stringify({ setup: { model: 'nemotron-3-nano' } }))
      const { latitude, longitude } = coords()
      ws.send(JSON.stringify({ realtimeInput: { location: { latitude, longitude } } }))
      void requestCurrentLocation(true)
    }

    ws.onclose = () => {
      setWsConnected(false)
    }

    ws.onerror = () => {
      addMsg('sys', 'WebSocket error.')
    }

    ws.onmessage = (e) => {
      let data: any
      try {
        data = JSON.parse(e.data)
      } catch {
        addMsg('sys', `Raw: ${String(e.data)}`)
        return
      }
      if (data.setupComplete) addMsg('sys', 'Setup complete.')
      const sc = data.serverContent ?? {}
      if (sc.inputTranscription?.text) addMsg('user', sc.inputTranscription.text)
      if (sc.outputTranscription?.text) addMsg('assistant', sc.outputTranscription.text)
      if (sc.proactiveAlert?.bullets?.length) {
        addMsg('assistant', `Proactive alert:\n- ${sc.proactiveAlert.bullets.join('\n- ')}`)
      }
      if (sc.turnComplete) addMsg('sys', 'Turn complete.')
    }
  }

  const disconnect = () => {
    wsRef.current?.close()
  }

  const sendChat = () => {
    const text = messageInput.trim()
    if (!text) return
    const { latitude, longitude } = coords()
    sendJson({ realtimeInput: { location: { latitude, longitude } } })
    if (sendJson({ clientContent: { turns: [{ role: 'user', parts: [{ text }] }] } })) {
      setMessageInput('')
    }
  }

  const get = async (url: string) => {
    const res = await fetch(url)
    const text = await res.text()
    try {
      return { status: res.status, body: JSON.parse(text) }
    } catch {
      return { status: res.status, body: text }
    }
  }

  const post = async (url: string, body?: unknown) => {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    })
    const text = await res.text()
    try {
      return { status: res.status, body: JSON.parse(text) }
    } catch {
      return { status: res.status, body: text }
    }
  }

  const runApi = async () => {
    const base = nemoclawBase.replace(/\/$/, '')
    const { latitude, longitude } = coords()
    const path = endpointPath
      .replace('{lat}', String(latitude))
      .replace('{lon}', String(longitude))
    const url = `${base}${path}`
    const body =
      path.startsWith('/v1/agent')
        ? { text: 'Give me a concise safety summary for around me.', latitude, longitude }
        : undefined
    try {
      const result = await post(url, body)
      setApiOut(`POST ${url} -> ${result.status}\n\n${JSON.stringify(result.body, null, 2)}`)
    } catch (e: any) {
      setApiOut(`Request failed: ${e.message}`)
    }
  }

  const hitMiddleware = async (path: '/health' | '/metrics' | '/admin/alert') => {
    const url = `http://localhost:8080${path}`
    const result = path === '/admin/alert' ? await post(url) : await get(url)
    const method = path === '/admin/alert' ? 'POST' : 'GET'
    setApiOut(`${method} ${url} -> ${result.status}\n\n${JSON.stringify(result.body, null, 2)}`)
  }

  useEffect(() => {
    return () => wsRef.current?.close()
  }, [])

  return (
    <div className="wrap">
      <div className="topbar">
        <div>
          <strong>Nemo Chat Test UI (React + TS)</strong>{' '}
          <span className={wsConnected ? 'ok' : 'bad'}>
            {wsConnected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
        <div className="row">
          <input value={wsUrl} onChange={(e) => setWsUrl(e.target.value)} />
          <input value={nemoclawBase} onChange={(e) => setNemoclawBase(e.target.value)} />
          <input value={lat} onChange={(e) => setLat(e.target.value)} />
          <input value={lon} onChange={(e) => setLon(e.target.value)} />
          <button className="alt" onClick={() => void requestCurrentLocation(false)} disabled={locating}>
            {locating ? 'Locating...' : 'Use Current Location'}
          </button>
          <button onClick={connect}>Connect</button>
          <button className="alt" onClick={disconnect}>Disconnect</button>
        </div>
        <div className="mini">{locState}</div>
      </div>

      <div className="grid">
        <div className="panel">
          <div className="chat">
            {messages.map((m, i) => (
              <div key={`${i}-${m.role}`} className={`msg ${m.role}`}>
                {m.text}
              </div>
            ))}
          </div>
          <div className="inputbar">
            <input
              value={messageInput}
              onChange={(e) => setMessageInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && sendChat()}
              placeholder="Ask about subway, restaurants, safety, landmarks..."
            />
            <button onClick={sendChat}>Send</button>
          </div>
        </div>

        <div className="panel">
          <div><strong>Quick API Tests</strong></div>
          <div className="row">
            <select value={endpointPath} onChange={(e) => setEndpointPath(e.target.value)}>
              <option value="/v1/agent">POST /v1/agent</option>
              <option value="/v1/situation_report?latitude={lat}&longitude={lon}">POST /v1/situation_report</option>
              <option value="/v1/hot_query?latitude={lat}&longitude={lon}">POST /v1/hot_query</option>
              <option value="/v1/cold_query?latitude={lat}&longitude={lon}&radius_meters=300">POST /v1/cold_query</option>
              <option value="/v1/collisions?latitude={lat}&longitude={lon}&radius_meters=300">POST /v1/collisions</option>
              <option value="/v1/accessibility?latitude={lat}&longitude={lon}&radius_meters=300">POST /v1/accessibility</option>
              <option value="/v1/heat?latitude={lat}&longitude={lon}">POST /v1/heat</option>
              <option value="/v1/edge_cases?latitude={lat}&longitude={lon}">POST /v1/edge_cases</option>
              <option value="/v1/cultural?latitude={lat}&longitude={lon}">POST /v1/cultural</option>
            </select>
            <button onClick={() => void runApi()}>Run</button>
          </div>
          <div className="row">
            <button className="alt" onClick={() => void hitMiddleware('/health')}>GET middleware /health</button>
            <button className="alt" onClick={() => void hitMiddleware('/metrics')}>GET middleware /metrics</button>
            <button className="warn" onClick={() => void hitMiddleware('/admin/alert')}>POST middleware /admin/alert</button>
          </div>
          <div className="mini">Last API Result</div>
          <pre className="api">{apiOut}</pre>
        </div>
      </div>
    </div>
  )
}

export default App
