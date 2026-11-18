import React, { useState, useEffect, useRef, useMemo } from 'react'
import {
  Zap,
  TrendingUp,
  TrendingDown,
  AlertTriangle,
  Copy,
  Check,
  Volume2,
  VolumeX,
  Radio,
  Clock,
  Target,
  ShieldAlert,
  ArrowUpRight,
  ArrowDownRight,
  ExternalLink,
  RefreshCw,
  Flame,
  CheckCircle2,
  XCircle,
  Search,
  Filter,
  BarChart3,
  Layers,
  Sparkles,
  Info,
  DollarSign,
  ChevronRight,
} from 'lucide-react'

// Safe INR Currency Formatter
const formatINR = (val, decimals = 2) => {
  const num = Number(val)
  if (isNaN(num) || !isFinite(num)) return '0.00'
  return num.toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

// =========================================================================
// HTML5 Web Audio API Synthesizer (Zero external dependencies & Autoplay Unlocked)
// =========================================================================
let audioCtxInstance = null

const getAudioContext = () => {
  if (typeof window === 'undefined') return null
  const AudioContextClass = window.AudioContext || window.webkitAudioContext
  if (!AudioContextClass) return null
  if (!audioCtxInstance) {
    audioCtxInstance = new AudioContextClass()
  }
  if (audioCtxInstance.state === 'suspended') {
    audioCtxInstance.resume().catch(() => {})
  }
  return audioCtxInstance
}

const playAudioCue = (type = 'BUY', soundEnabled = true) => {
  if (!soundEnabled) return
  try {
    const ctx = getAudioContext()
    if (!ctx) return

    const now = ctx.currentTime

    if (type === 'REVERSAL') {
      // Urgent warble siren (sawtooth 880Hz -> 440Hz -> 920Hz)
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sawtooth'
      osc.connect(gain)
      gain.connect(ctx.destination)

      osc.frequency.setValueAtTime(880, now)
      osc.frequency.setValueAtTime(440, now + 0.12)
      osc.frequency.setValueAtTime(920, now + 0.24)
      osc.frequency.setValueAtTime(440, now + 0.36)

      gain.gain.setValueAtTime(0.3, now)
      gain.gain.exponentialRampToValueAtTime(0.01, now + 0.55)

      osc.start(now)
      osc.stop(now + 0.55)
    } else if (type === 'SELL') {
      // Resolving chime (sine 784Hz -> 523Hz, G5 -> C5)
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.connect(gain)
      gain.connect(ctx.destination)

      osc.frequency.setValueAtTime(783.99, now)
      osc.frequency.setValueAtTime(523.25, now + 0.15)

      gain.gain.setValueAtTime(0.25, now)
      gain.gain.exponentialRampToValueAtTime(0.01, now + 0.4)

      osc.start(now)
      osc.stop(now + 0.4)
    } else {
      // Crisp premium BUY chime (sine 587Hz -> 880Hz, D5 -> A5)
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.connect(gain)
      gain.connect(ctx.destination)

      osc.frequency.setValueAtTime(587.33, now)
      osc.frequency.setValueAtTime(880.0, now + 0.1)

      gain.gain.setValueAtTime(0.25, now)
      gain.gain.exponentialRampToValueAtTime(0.01, now + 0.4)

      osc.start(now)
      osc.stop(now + 0.4)
    }
  } catch (err) {
    console.warn('Audio cue failed or blocked by browser autoplay policy:', err)
  }
}

// Resilient Clipboard Copy Helper with execCommand fallback
const copyTextToClipboard = async (text) => {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Fallback below
    }
  }

  // Fallback for non-secure context or restricted iframes
  try {
    const textArea = document.createElement('textarea')
    textArea.value = text
    textArea.style.position = 'fixed'
    textArea.style.left = '-999999px'
    textArea.style.top = '-999999px'
    document.body.appendChild(textArea)
    textArea.focus()
    textArea.select()
    const successful = document.execCommand('copy')
    document.body.removeChild(textArea)
    return successful
  } catch (err) {
    console.error('Fallback clipboard copy failed:', err)
    return false
  }
}

// Quick Broker Launch Links
const BROKER_LINKS = [
  { name: 'Zerodha Kite', url: 'https://kite.zerodha.com', color: 'from-orange-500 to-amber-600', badge: 'Kite' },
  { name: 'Groww', url: 'https://groww.in/stocks/user/explore', color: 'from-emerald-500 to-teal-600', badge: 'Groww' },
  { name: 'Angel One', url: 'https://trade.angelone.in', color: 'from-blue-600 to-indigo-600', badge: 'Angel' },
  { name: 'Dhan', url: 'https://web.dhan.co', color: 'from-purple-600 to-violet-600', badge: 'Dhan' },
  { name: 'Upstox', url: 'https://pro.upstox.com', color: 'from-cyan-600 to-blue-700', badge: 'Upstox' },
]

export default function BuggyDashboard() {
  const [activeSignals, setActiveSignals] = useState([])
  const [completedSignals, setCompletedSignals] = useState([])
  const [isConnected, setIsConnected] = useState(false)
  const [lastHeartbeat, setLastHeartbeat] = useState(null)
  const [soundEnabled, setSoundEnabled] = useState(true)
  const [copiedId, setCopiedId] = useState(null)
  const [copiedStrikesId, setCopiedStrikesId] = useState(null)
  const [isLoadingInitial, setIsLoadingInitial] = useState(true)
  const [historyFilter, setHistoryFilter] = useState('ALL') // ALL | WINS | LOSSES
  const [searchQuery, setSearchQuery] = useState('')
  const [audioUnlocked, setAudioUnlocked] = useState(false)

  const eventSourceRef = useRef(null)
  const reconnectTimeoutRef = useRef(null)
  const soundEnabledRef = useRef(soundEnabled)

  useEffect(() => {
    soundEnabledRef.current = soundEnabled
  }, [soundEnabled])

  // Global user gesture listener to unlock Web Audio API automatically
  useEffect(() => {
    const unlockAudio = () => {
      const ctx = getAudioContext()
      if (ctx && ctx.state !== 'suspended') {
        setAudioUnlocked(true)
      }
    }

    window.addEventListener('click', unlockAudio, { once: true })
    window.addEventListener('keydown', unlockAudio, { once: true })

    return () => {
      window.removeEventListener('click', unlockAudio)
      window.removeEventListener('keydown', unlockAudio)
    }
  }, [])

  // =========================================================================
  // 1. Initial State Hydration via /api/buggy/overview
  // =========================================================================
  const fetchOverview = async () => {
    try {
      setIsLoadingInitial(true)
      const res = await fetch('/api/buggy/overview')
      if (res.ok) {
        const data = await res.json()
        if (data.active_signals) {
          setActiveSignals(data.active_signals)
        }
        if (data.completed_signals) {
          setCompletedSignals(data.completed_signals)
        }
      }
    } catch (err) {
      console.warn('Could not fetch Buggy initial overview:', err)
    } finally {
      setIsLoadingInitial(false)
    }
  }

  // =========================================================================
  // 2. Establish Server-Sent Events (SSE) Stream to /api/buggy/stream
  // =========================================================================
  useEffect(() => {
    fetchOverview()

    const connectBuggyStream = () => {
      try {
        if (eventSourceRef.current) {
          eventSourceRef.current.close()
        }

        const es = new EventSource('/api/buggy/stream')
        eventSourceRef.current = es

        es.onopen = () => {
          setIsConnected(true)
        }

        es.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data)

            // Keep-alive heartbeat
            if (data.type === 'HEARTBEAT') {
              setIsConnected(true)
              setLastHeartbeat(new Date().toLocaleTimeString())
              return
            }

            if (data.type === 'BUGGY_CONNECTED') {
              setIsConnected(true)
              setLastHeartbeat(new Date().toLocaleTimeString())
              return
            }

            // A. ON BUY_SIGNAL: Push new "Active Trade Idea" card to the top with audio chime
            if (data.type === 'BUY_SIGNAL') {
              setActiveSignals((prev) => {
                const exists = prev.find((s) => s.position_id === data.position_id)
                if (exists) {
                  return prev.map((s) => (s.position_id === data.position_id ? { ...s, ...data } : s))
                }
                return [data, ...prev]
              })
              playAudioCue('BUY', soundEnabledRef.current)
            }

            // B. ON ACTIVE_POSITION_SYNC: Keep active positions fresh without redundant beep
            else if (data.type === 'ACTIVE_POSITION_SYNC') {
              setActiveSignals((prev) => {
                const exists = prev.find((s) => s.position_id === data.position_id)
                if (exists) {
                  return prev.map((s) => (s.position_id === data.position_id ? { ...s, ...data } : s))
                }
                return [...prev, data]
              })
            }

            // C. ON REVERSAL_ALERT: Flash card background red and sound urgent warning siren
            else if (data.type === 'REVERSAL_ALERT') {
              setActiveSignals((prev) =>
                prev.map((s) => {
                  if (s.position_id === data.position_id) {
                    return {
                      ...s,
                      isReversal: true,
                      reversalDetails: data,
                      reversalTimestamp: data.timestamp || new Date().toISOString(),
                    }
                  }
                  return s
                })
              )
              playAudioCue('REVERSAL', soundEnabledRef.current)
            }

            // D. ON SELL_SIGNAL: Move card to Recent Completed Signals list with resolving chime
            else if (data.type === 'SELL_SIGNAL') {
              setActiveSignals((prev) => prev.filter((s) => s.position_id !== data.position_id))
              setCompletedSignals((prev) => {
                const exists = prev.find((s) => s.position_id === data.position_id)
                if (exists) {
                  return prev.map((s) => (s.position_id === data.position_id ? { ...s, ...data } : s))
                }
                return [data, ...prev].slice(0, 50)
              })
              playAudioCue('SELL', soundEnabledRef.current)
            }
          } catch (err) {
            console.error('Error parsing Buggy SSE event:', err)
          }
        }

        es.onerror = () => {
          setIsConnected(false)
          es.close()
          clearTimeout(reconnectTimeoutRef.current)
          reconnectTimeoutRef.current = setTimeout(connectBuggyStream, 4000)
        }
      } catch (err) {
        setIsConnected(false)
        console.warn('Buggy SSE stream error:', err)
        clearTimeout(reconnectTimeoutRef.current)
        reconnectTimeoutRef.current = setTimeout(connectBuggyStream, 5000)
      }
    }

    connectBuggyStream()

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
      }
      clearTimeout(reconnectTimeoutRef.current)
    }
  }, [])

  // =========================================================================
  // 3. Format & Copy Full Order Ticket for Broker Terminal
  // =========================================================================
  const handleCopyToBroker = async (signal) => {
    const legsText = (signal.legs || [])
      .map(
        (l) =>
          `${l.action || 'BUY'} ${signal.ticker} ${l.strike} ${l.option_type || 'CE'} @ ₹${formatINR(
            l.fill_price || signal.recommended_entry_premium
          )}`
      )
      .join(' | ')

    const defaultLotMultiplier = (signal.ticker && signal.ticker.toUpperCase() === 'BANKNIFTY') ? 30 : 65
    const totalUnits = signal.total_quantity || (signal.lot_size && signal.qty ? signal.lot_size * signal.qty : (signal.qty ? signal.qty * defaultLotMultiplier : defaultLotMultiplier))

    const orderSummary = `[TRADING BUGGY REAL-MONEY SIGNAL]\n` +
      `Instrument: ${signal.ticker}\n` +
      `Strategy: ${signal.strategy_name}\n` +
      `Bias: ${signal.bias || 'DIRECTIONAL'}\n` +
      `Legs: ${legsText || 'N/A'}\n` +
      `Recommended Net Entry: ₹${formatINR(signal.recommended_entry_premium)} (${signal.trade_type || 'DEBIT'})\n` +
      `Target Profit Exit: +₹${formatINR(signal.target)}\n` +
      `Stop-Loss Exit: -₹${formatINR(signal.stop_loss)}\n` +
      `Quantity: ${signal.qty || 1} Lot (${totalUnits} units)\n` +
      `Est. Capital: ₹${formatINR(signal.capital_allocated)}`

    const ok = await copyTextToClipboard(orderSummary)
    if (ok) {
      setCopiedId(signal.position_id)
      setTimeout(() => setCopiedId(null), 2500)
    }
  }

  // =========================================================================
  // 4. Quick Copy Strikes Only (For Search Bar in Kite / Groww)
  // =========================================================================
  const handleCopyStrikesOnly = async (signal) => {
    const strikesText = (signal.legs || [])
      .map((l) => `${signal.ticker} ${l.strike} ${l.option_type || 'CE'}`)
      .join(', ')

    const ok = await copyTextToClipboard(strikesText || signal.ticker)
    if (ok) {
      setCopiedStrikesId(signal.position_id)
      setTimeout(() => setCopiedStrikesId(null), 2500)
    }
  }

  // =========================================================================
  // 5. Aggregate Performance Analytics
  // =========================================================================
  const stats = useMemo(() => {
    const total = completedSignals.length
    if (total === 0) {
      return { total: 0, wins: 0, losses: 0, winRate: 0, netPnl: 0, avgWin: 0, avgLoss: 0 }
    }
    let wins = 0
    let totalWinPnl = 0
    let losses = 0
    let totalLossPnl = 0
    let netPnl = 0

    completedSignals.forEach((s) => {
      const pnl = Number(s.pnl || 0)
      netPnl += pnl
      if (pnl >= 0) {
        wins++
        totalWinPnl += pnl
      } else {
        losses++
        totalLossPnl += Math.abs(pnl)
      }
    })

    return {
      total,
      wins,
      losses,
      winRate: Math.round((wins / total) * 100),
      netPnl,
      avgWin: wins > 0 ? totalWinPnl / wins : 0,
      avgLoss: losses > 0 ? totalLossPnl / losses : 0,
    }
  }, [completedSignals])

  // Filtered Completed Signals
  const filteredCompletedSignals = useMemo(() => {
    return completedSignals.filter((item) => {
      // Filter by win/loss status
      if (historyFilter === 'WINS' && (item.pnl || 0) < 0) return false
      if (historyFilter === 'LOSSES' && (item.pnl || 0) >= 0) return false

      // Filter by search query
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase()
        const ticker = (item.ticker || '').toLowerCase()
        const strat = (item.strategy_name || '').toLowerCase()
        const reason = (item.exit_reason || '').toLowerCase()
        return ticker.includes(q) || strat.includes(q) || reason.includes(q)
      }
      return true
    })
  }, [completedSignals, historyFilter, searchQuery])

  return (
    <div className="flex-1 flex flex-col overflow-y-auto bg-black text-slate-100 font-sans p-6 space-y-6">
      {/* =========================================================================
          HUD BANNER & CONTROLS (DARK MODE WITH NEON AMBER / GOLD ACCENTS)
      ========================================================================= */}
      <div className="rounded-2xl border border-amber-500/40 bg-gradient-to-r from-slate-950 via-slate-900 to-amber-950/30 p-5 shadow-2xl shadow-amber-950/20 backdrop-blur-xl relative overflow-hidden">
        {/* Ambient neon backdrop glow */}
        <div className="absolute top-0 right-0 w-96 h-96 bg-amber-500/10 rounded-full blur-3xl pointer-events-none" />
        <div className="absolute bottom-0 left-1/3 w-80 h-40 bg-yellow-500/5 rounded-full blur-2xl pointer-events-none" />

        <div className="flex flex-wrap items-center justify-between gap-4 relative z-10">
          <div className="space-y-1">
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-xl bg-gradient-to-tr from-amber-500 to-yellow-400 text-black font-extrabold shadow-lg shadow-amber-500/30 animate-pulse">
                <Flame className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-xl font-black text-white tracking-wider flex items-center gap-2.5">
                  <span className="text-transparent bg-clip-text bg-gradient-to-r from-amber-400 via-yellow-200 to-amber-500">
                    TRADING BUGGY
                  </span>
                  <span className="text-[11px] font-mono font-bold px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/50 shadow-sm">
                    REAL-MONEY TRADE SIGNALS
                  </span>
                </h2>
                <p className="text-xs text-slate-400 font-mono mt-0.5">
                  Actionable trade signals emitted for manual execution in your personal Demat terminal (Zerodha, Angel One, Groww, Dhan).
                </p>
              </div>
            </div>
          </div>

          {/* Quick HUD Metrics & Audio Toggles */}
          <div className="flex flex-wrap items-center gap-2.5">
            {/* Live SSE Status Pill */}
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-slate-950/80 border border-slate-800 text-xs font-mono shadow-inner">
              <span className="relative flex h-2 w-2">
                <span
                  className={`absolute inline-flex h-full w-full rounded-full opacity-75 ${
                    isConnected ? 'bg-amber-400 animate-ping' : 'bg-rose-500'
                  }`}
                />
                <span
                  className={`relative inline-flex rounded-full h-2 w-2 ${
                    isConnected ? 'bg-amber-400' : 'bg-rose-500'
                  }`}
                />
              </span>
              <span className={isConnected ? 'text-amber-300 font-bold' : 'text-rose-400 font-bold'}>
                {isConnected ? 'LIVE SIGNAL FEED ACTIVE' : 'CONNECTING...'}
              </span>
              {lastHeartbeat && (
                <span className="text-[10px] text-slate-500 border-l border-slate-800 pl-2">
                  {lastHeartbeat}
                </span>
              )}
            </div>

            {/* Audio Toggle */}
            <button
              onClick={() => {
                getAudioContext()
                setSoundEnabled(!soundEnabled)
              }}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs font-bold transition-all border ${
                soundEnabled
                  ? 'bg-amber-500/15 text-amber-300 border-amber-500/50 hover:bg-amber-500/25 shadow-sm shadow-amber-500/20'
                  : 'bg-slate-900 text-slate-400 border-slate-800 hover:text-slate-200'
              }`}
              title="Toggle audio notification chimes for Buy, Sell, and Reversal events"
            >
              {soundEnabled ? <Volume2 className="w-3.5 h-3.5 text-amber-400" /> : <VolumeX className="w-3.5 h-3.5" />}
              <span>{soundEnabled ? 'Audio: ON' : 'Audio: MUTED'}</span>
            </button>

            {/* Test Audio Button */}
            <button
              onClick={() => {
                getAudioContext()
                playAudioCue('BUY', true)
              }}
              className="px-3 py-1.5 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-xs font-mono text-slate-300 hover:text-white transition-colors"
              title="Test browser audio chime"
            >
              🔔 Test Chime
            </button>

            {/* Refresh Button */}
            <button
              onClick={fetchOverview}
              className="p-2 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-white transition-colors"
              title="Re-query initial Buggy state"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isLoadingInitial ? 'animate-spin text-amber-400' : ''}`} />
            </button>
          </div>
        </div>

        {/* Real Money Capital & Reversal Advisory */}
        <div className="mt-4 p-3.5 rounded-xl border border-amber-500/25 bg-amber-950/20 text-xs text-amber-200/90 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <ShieldAlert className="w-4 h-4 text-amber-400 flex-shrink-0" />
            <span>
              <strong>MANUAL DEMAT EXECUTION PROTOCOL:</strong> Copy the exact option strikes into your broker terminal to buy. If a <strong className="text-rose-300 underline underline-offset-2">Reversal Alert</strong> is flagged, manually square off your Demat position early to avoid incurring full Stop-Loss damage!
            </span>
          </div>
          <span className="text-[10px] font-mono font-bold px-2.5 py-0.5 rounded-md bg-amber-500/20 text-amber-300 border border-amber-500/40 whitespace-nowrap">
            Manual Execution Only
          </span>
        </div>
      </div>

      {/* =========================================================================
          BROKER QUICK LAUNCHPAD
      ========================================================================= */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-3.5 rounded-xl bg-slate-950/70 border border-slate-800/80">
        <div className="flex items-center gap-2 text-xs font-mono text-slate-400">
          <ExternalLink className="w-3.5 h-3.5 text-amber-400" />
          <span className="font-bold text-slate-300">Quick Broker Launchpad:</span>
          <span className="hidden sm:inline text-slate-500">Open your terminal in a new tab:</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {BROKER_LINKS.map((broker) => (
            <a
              key={broker.name}
              href={broker.url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 px-3 py-1 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-700/80 text-xs font-mono text-slate-300 hover:text-white transition-all hover:border-amber-500/40 hover:scale-105 active:scale-95"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
              <span>{broker.name}</span>
            </a>
          ))}
        </div>
      </div>

      {/* =========================================================================
          SECTION 1: ACTIVE REAL-MONEY TRADE IDEAS (PUSHED ON BUY_SIGNAL)
      ========================================================================= */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="p-1.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-400">
              <Zap className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-base font-black text-white tracking-wide uppercase">
                Active Trade Ideas ({activeSignals.length})
              </h3>
            </div>
          </div>
          <span className="text-xs font-mono text-slate-400 flex items-center gap-1.5">
            {activeSignals.length === 0 ? (
              <>
                <span className="w-2 h-2 rounded-full bg-amber-400/60 animate-ping" />
                <span>Radar Scanning Market Conditions</span>
              </>
            ) : (
              <span className="text-amber-300 font-bold">{activeSignals.length} Actionable Setup(s) Active</span>
            )}
          </span>
        </div>

        {activeSignals.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-800 bg-slate-950/60 p-12 text-center flex flex-col items-center justify-center space-y-3">
            <div className="p-3.5 rounded-2xl bg-amber-500/10 border border-amber-500/30 text-amber-400 shadow-lg shadow-amber-500/10">
              <Radio className="w-8 h-8 animate-pulse" />
            </div>
            <h4 className="text-sm font-bold text-slate-200">Radar Scanning Live Market Feeds for Trade Setups</h4>
            <p className="text-xs text-slate-400 max-w-md font-mono leading-relaxed">
              When high-conviction opportunities pass quantitative criteria and regime gating, actionable cards with exact option strikes, net premium, target, and stop-loss will populate here.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-5">
            {activeSignals.map((signal) => {
              const isReversal = !!signal.isReversal
              const isBullish = signal.bias === 'BULLISH'
              const isBearish = signal.bias === 'BEARISH'
              const isCopied = copiedId === signal.position_id
              const isStrikesCopied = copiedStrikesId === signal.position_id

              // Calculate Risk / Reward
              const targetVal = Number(signal.target || 0)
              const slVal = Math.abs(Number(signal.stop_loss || 1))
              const rrRatio = slVal > 0 ? (targetVal / slVal).toFixed(1) : '1.0'

              return (
                <div
                  key={signal.position_id}
                  className={`rounded-2xl border-2 transition-all p-6 shadow-2xl relative overflow-hidden ${
                    isReversal
                      ? 'border-rose-500 bg-rose-950/70 shadow-rose-950/80 animate-pulse ring-4 ring-rose-500/40'
                      : 'border-amber-500/50 bg-gradient-to-b from-slate-900/95 via-slate-900/90 to-slate-950/95 shadow-amber-950/20 hover:border-amber-400'
                  }`}
                >
                  {/* URGENT REVERSAL ALERT FLASHING BANNER */}
                  {isReversal && (
                    <div className="mb-5 -mx-6 -mt-6 p-4 bg-gradient-to-r from-rose-600 via-red-600 to-rose-700 text-white flex items-center justify-between shadow-lg">
                      <div className="flex items-center gap-3">
                        <AlertTriangle className="w-6 h-6 text-white animate-bounce flex-shrink-0" />
                        <div>
                          <div className="text-sm font-black tracking-wider uppercase flex items-center gap-2">
                            <span>⚠️ REVERSAL DETECTED: Square Off Demat Position Early</span>
                            <span className="text-[10px] bg-black/40 px-2 py-0.5 rounded font-mono font-bold">
                              ACTION REQUIRED
                            </span>
                          </div>
                          <div className="text-xs font-mono text-rose-100 mt-0.5">
                            {signal.reversalDetails?.detail ||
                              'Underlying spot price turned adverse against trade bias. Square off manually in your Demat account (Zerodha, Groww, Angel One) to limit loss.'}
                          </div>
                        </div>
                      </div>
                      <span className="hidden sm:inline-block px-3 py-1 rounded-lg bg-black/40 text-rose-200 font-mono text-xs font-bold border border-rose-400/50">
                        CUT LOSS NOW
                      </span>
                    </div>
                  )}

                  {/* Top Row: Symbol, Strategy, Bias Pill, and Timestamp */}
                  <div className="flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-slate-800/80">
                    <div className="flex items-center gap-3">
                      <span className="text-3xl font-black font-mono tracking-wider text-amber-300 drop-shadow-sm">
                        {signal.ticker}
                      </span>
                      <span className="px-3 py-1 rounded-xl text-xs font-bold bg-slate-800 text-slate-200 border border-slate-700 font-mono">
                        {signal.strategy_name}
                      </span>
                      <span
                        className={`flex items-center gap-1 px-3 py-1 rounded-xl text-xs font-black tracking-wider uppercase border font-mono ${
                          isBullish
                            ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40 shadow-sm shadow-emerald-500/20'
                            : isBearish
                            ? 'bg-rose-500/20 text-rose-300 border-rose-500/40 shadow-sm shadow-rose-500/20'
                            : 'bg-cyan-500/20 text-cyan-300 border-cyan-500/40'
                        }`}
                      >
                        {isBullish ? (
                          <ArrowUpRight className="w-3.5 h-3.5" />
                        ) : isBearish ? (
                          <ArrowDownRight className="w-3.5 h-3.5" />
                        ) : null}
                        <span>{signal.bias || 'DIRECTIONAL'}</span>
                      </span>

                      <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-800/80 text-amber-400 border border-slate-700">
                        R:R 1:{rrRatio}
                      </span>
                    </div>

                    <div className="flex items-center gap-3 text-xs font-mono text-slate-400">
                      <span className="flex items-center gap-1.5">
                        <Clock className="w-3.5 h-3.5 text-amber-400" />
                        <span>
                          {signal.entry_timestamp
                            ? new Date(signal.entry_timestamp).toLocaleTimeString()
                            : 'Live'}
                        </span>
                      </span>
                      <span className="px-2.5 py-0.5 rounded bg-slate-950 text-slate-500 border border-slate-800 font-bold">
                        Signal #{signal.position_id}
                      </span>
                    </div>
                  </div>

                  {/* Main Metrics Matrix */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 py-5 border-b border-slate-800/80">
                    {/* Recommended Entry Premium */}
                    <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800/80 space-y-1">
                      <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider block font-mono">
                        Recommended Entry Net Premium
                      </span>
                      <div className="text-2xl font-black font-mono text-white flex items-baseline gap-1.5">
                        <span>₹{formatINR(signal.recommended_entry_premium || Math.abs(signal.net_premium || 0))}</span>
                        <span className="text-xs font-bold text-amber-400 font-sans">
                          {signal.trade_type === 'CREDIT' ? '(Credit)' : '(Debit)'}
                        </span>
                      </div>
                    </div>

                    {/* Target Profit Exit */}
                    <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800/80 space-y-1">
                      <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider block font-mono">
                        Target Profit Exit
                      </span>
                      <div className="text-2xl font-black font-mono text-emerald-400">
                        +₹{formatINR(signal.target)}
                      </div>
                    </div>

                    {/* Stop Loss Exit */}
                    <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800/80 space-y-1">
                      <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider block font-mono">
                        Recommended Stop-Loss
                      </span>
                      <div className="text-2xl font-black font-mono text-rose-400">
                        -₹{formatINR(signal.stop_loss)}
                      </div>
                    </div>

                    {/* Lot Size & Quantity */}
                    <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800/80 space-y-1">
                      <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider block font-mono">
                        Quantity / Lot Size
                      </span>
                      <div className="text-2xl font-black font-mono text-cyan-300">
                        {signal.qty || 1} Lot{' '}
                        <span className="text-xs text-slate-400 font-sans">
                          ({signal.total_quantity || (signal.lot_size && signal.qty ? signal.lot_size * signal.qty : (signal.qty ? signal.qty * ((signal.ticker && signal.ticker.toUpperCase() === 'BANKNIFTY') ? 30 : 65) : ((signal.ticker && signal.ticker.toUpperCase() === 'BANKNIFTY') ? 30 : 65)))} units)
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Option Strike Legs Breakdown */}
                  <div className="py-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-bold text-slate-400 uppercase tracking-wider font-mono block">
                        Execution Option Legs:
                      </span>
                      <button
                        onClick={() => handleCopyStrikesOnly(signal)}
                        className="text-[11px] font-mono text-amber-400 hover:text-amber-300 flex items-center gap-1 transition-colors"
                        title="Copy only strike symbols to paste in search bar"
                      >
                        {isStrikesCopied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                        <span>{isStrikesCopied ? 'Strikes Copied!' : 'Copy Strikes Only'}</span>
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {(signal.legs || []).map((leg, lIdx) => (
                        <div
                          key={lIdx}
                          className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg border font-mono text-xs ${
                            leg.action === 'BUY'
                              ? 'bg-emerald-950/50 border-emerald-500/50 text-emerald-200'
                              : 'bg-rose-950/50 border-rose-500/50 text-rose-200'
                          }`}
                        >
                          <span
                            className={`font-black text-[10px] px-1.5 py-0.5 rounded ${
                              leg.action === 'BUY' ? 'bg-emerald-500 text-black' : 'bg-rose-500 text-white'
                            }`}
                          >
                            {leg.action}
                          </span>
                          <span className="font-bold">
                            {signal.ticker} {leg.strike} {leg.option_type}
                          </span>
                          {leg.fill_price && (
                            <span className="text-slate-400 text-[11px]">
                              @ ₹{formatINR(leg.fill_price)}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Quantitative Rationale */}
                  {signal.thesis && (
                    <div className="text-xs text-slate-400 font-mono bg-slate-950/60 p-3 rounded-xl border border-slate-800/60 leading-relaxed">
                      <span className="text-amber-400 font-bold mr-2">Core Thesis:</span>
                      {signal.thesis}
                    </div>
                  )}

                  {/* BOTTOM ACTION BAR */}
                  <div className="mt-5 pt-4 border-t border-slate-800/80 flex flex-wrap items-center justify-between gap-3">
                    <div className="text-xs text-slate-400 font-mono">
                      <span>Est. Capital Allocation: </span>
                      <span className="text-white font-bold">₹{formatINR(signal.capital_allocated)}</span>
                    </div>

                    <div className="flex items-center gap-2.5">
                      <button
                        onClick={() => handleCopyStrikesOnly(signal)}
                        className="px-3.5 py-2 rounded-xl border border-slate-700 bg-slate-900 hover:bg-slate-800 text-slate-300 font-mono text-xs font-bold transition-all"
                      >
                        {isStrikesCopied ? 'STRIKES COPIED ✓' : 'COPY STRIKES'}
                      </button>

                      <button
                        onClick={() => handleCopyToBroker(signal)}
                        className={`flex items-center gap-2 px-5 py-2.5 rounded-xl font-mono text-xs font-bold transition-all shadow-lg active:scale-95 ${
                          isCopied
                            ? 'bg-emerald-500 text-black shadow-emerald-500/40 border border-emerald-400'
                            : 'bg-gradient-to-r from-amber-500 to-yellow-400 hover:from-amber-400 hover:to-yellow-300 text-black shadow-amber-500/25 border border-amber-400'
                        }`}
                      >
                        {isCopied ? <Check className="w-4 h-4 stroke-[3]" /> : <Copy className="w-4 h-4" />}
                        <span>{isCopied ? 'ORDER TICKET COPIED! ✓' : 'COPY ORDER TO BROKER'}</span>
                      </button>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* =========================================================================
          SECTION 2: RECENT COMPLETED SIGNALS (MOVED ON SELL_SIGNAL)
      ========================================================================= */}
      <div className="space-y-4 pt-4">
        {/* Performance Overview Ribbon */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800/80 space-y-1">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider block font-mono">
              Total Signals Completed
            </span>
            <div className="text-2xl font-black font-mono text-white">{stats.total}</div>
          </div>

          <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800/80 space-y-1">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider block font-mono">
              Win Rate %
            </span>
            <div className={`text-2xl font-black font-mono ${stats.winRate >= 50 ? 'text-emerald-400' : 'text-amber-400'}`}>
              {stats.winRate}% <span className="text-xs text-slate-400 font-sans">({stats.wins}W / {stats.losses}L)</span>
            </div>
          </div>

          <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800/80 space-y-1">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider block font-mono">
              Net Realized P&L
            </span>
            <div className={`text-2xl font-black font-mono ${stats.netPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {stats.netPnl >= 0 ? '+' : ''}₹{formatINR(stats.netPnl)}
            </div>
          </div>

          <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800/80 space-y-1">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider block font-mono">
              Avg Win / Avg Loss
            </span>
            <div className="text-sm font-black font-mono text-slate-200 flex items-center gap-2 pt-1">
              <span className="text-emerald-400">+₹{formatINR(stats.avgWin, 0)}</span>
              <span className="text-slate-600">/</span>
              <span className="text-rose-400">-₹{formatINR(stats.avgLoss, 0)}</span>
            </div>
          </div>
        </div>

        {/* Section Header & Filters */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
          <div className="flex items-center gap-2.5">
            <div className="p-1.5 rounded-lg bg-cyan-500/10 border border-cyan-500/30 text-cyan-400">
              <Clock className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-base font-black text-white tracking-wide uppercase">
                Completed Signal History ({filteredCompletedSignals.length})
              </h3>
            </div>
          </div>

          {/* Search & Win/Loss Filter Tabs */}
          <div className="flex flex-wrap items-center gap-2">
            {/* Search Input */}
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                placeholder="Filter ticker / strategy..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-8 pr-3 py-1.5 rounded-xl bg-slate-900 border border-slate-800 text-xs font-mono text-slate-200 placeholder-slate-500 focus:outline-none focus:border-amber-500/50 w-48"
              />
            </div>

            {/* Win/Loss Filter Buttons */}
            <div className="flex items-center p-0.5 rounded-xl bg-slate-900 border border-slate-800 text-xs font-mono">
              <button
                onClick={() => setHistoryFilter('ALL')}
                className={`px-3 py-1 rounded-lg transition-all ${
                  historyFilter === 'ALL'
                    ? 'bg-amber-500 text-black font-bold shadow-sm'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                All
              </button>
              <button
                onClick={() => setHistoryFilter('WINS')}
                className={`px-3 py-1 rounded-lg transition-all ${
                  historyFilter === 'WINS'
                    ? 'bg-emerald-500 text-black font-bold shadow-sm'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                Wins
              </button>
              <button
                onClick={() => setHistoryFilter('LOSSES')}
                className={`px-3 py-1 rounded-lg transition-all ${
                  historyFilter === 'LOSSES'
                    ? 'bg-rose-500 text-white font-bold shadow-sm'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                Losses
              </button>
            </div>
          </div>
        </div>

        {/* Completed Signals Table */}
        <div className="rounded-2xl border border-slate-800 bg-slate-950/60 overflow-hidden shadow-xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left font-mono text-xs border-collapse">
              <thead className="bg-slate-900/90 text-slate-400 border-b border-slate-800">
                <tr>
                  <th className="py-3 px-4">Exit Time</th>
                  <th className="py-3 px-4">Instrument</th>
                  <th className="py-3 px-4">Strategy & Legs</th>
                  <th className="py-3 px-4">Exit Reason & Instruction</th>
                  <th className="py-3 px-4">Exit Premium</th>
                  <th className="py-3 px-4 text-right">Realized P&L</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {filteredCompletedSignals.length === 0 ? (
                  <tr>
                    <td colSpan="6" className="text-center py-10 text-slate-500 font-sans text-xs">
                      {completedSignals.length === 0
                        ? 'No completed trade signals yet today.'
                        : 'No trade signals match your search or filter.'}
                    </td>
                  </tr>
                ) : (
                  filteredCompletedSignals.map((item, idx) => {
                    const isWin = (item.pnl || 0) >= 0
                    const legs = item.legs || []

                    return (
                      <tr key={idx} className="hover:bg-slate-900/50 transition-colors">
                        <td className="py-3 px-4 text-slate-400 text-[11px] whitespace-nowrap">
                          {item.exit_timestamp
                            ? new Date(item.exit_timestamp).toLocaleTimeString()
                            : '—'}
                        </td>

                        <td className="py-3 px-4">
                          <span className="font-black text-amber-300">{item.ticker}</span>
                        </td>

                        <td className="py-3 px-4">
                          <div className="space-y-1">
                            <span className="text-slate-200 font-sans font-medium block">
                              {item.strategy_name}
                            </span>
                            {legs.length > 0 && (
                              <div className="flex flex-wrap gap-1">
                                {legs.map((leg, lIdx) => (
                                  <span
                                    key={lIdx}
                                    className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                                      leg.action === 'BUY'
                                        ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-500/30'
                                        : 'bg-rose-950/80 text-rose-300 border border-rose-500/30'
                                    }`}
                                  >
                                    {leg.action} {leg.strike} {leg.option_type}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>
                        </td>

                        <td className="py-3 px-4">
                          <div className="space-y-1">
                            <span
                              className={`inline-block px-2 py-0.5 rounded text-[11px] font-bold border ${
                                item.exit_reason?.includes('Target')
                                  ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
                                  : item.exit_reason?.includes('Stop')
                                  ? 'bg-rose-500/20 text-rose-300 border-rose-500/40'
                                  : 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                              }`}
                            >
                              {item.exit_reason || item.status}
                            </span>
                            <div className="text-[11px] text-slate-400 font-sans">
                              {item.instruction || 'Square off in your Demat account (Zerodha, Groww, Angel One).'}
                            </div>
                          </div>
                        </td>

                        <td className="py-3 px-4 text-slate-200 whitespace-nowrap">
                          {item.exit_price ? `₹${formatINR(item.exit_price)}` : '—'}
                        </td>

                        <td className="py-3 px-4 text-right whitespace-nowrap font-bold">
                          <span
                            className={`inline-block px-2.5 py-1 rounded text-xs ${
                              isWin
                                ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/25'
                                : 'bg-rose-500/10 text-rose-400 border border-rose-500/25'
                            }`}
                          >
                            {isWin ? '+' : ''}₹{formatINR(item.pnl)}
                          </span>
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
