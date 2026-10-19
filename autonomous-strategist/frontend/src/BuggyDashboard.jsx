import React, { useState, useEffect, useRef } from 'react'
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
  Sparkles,
  ExternalLink,
  Bell,
  RefreshCw,
  Flame,
  CheckCircle2,
  XCircle,
} from 'lucide-react'

const BACKEND_URL = 'http://localhost:8000'

// Safe INR Formatter
const formatINR = (val, decimals = 2) => {
  const num = Number(val)
  if (isNaN(num) || !isFinite(num)) return '0.00'
  return num.toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

// =========================================================================
// HTML5 Web Audio API Synthesizer (Zero external dependencies)
// =========================================================================
const playAudioCue = (type = 'BUY', soundEnabled = true) => {
  if (!soundEnabled) return
  try {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext
    if (!AudioContextClass) return
    const ctx = new AudioContextClass()

    if (type === 'REVERSAL') {
      // Urgent warble warning siren (sawtooth 880Hz -> 440Hz -> 880Hz)
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sawtooth'
      osc.connect(gain)
      gain.connect(ctx.destination)

      const now = ctx.currentTime
      osc.frequency.setValueAtTime(880, now)
      osc.frequency.setValueAtTime(440, now + 0.12)
      osc.frequency.setValueAtTime(920, now + 0.24)
      osc.frequency.setValueAtTime(440, now + 0.36)

      gain.gain.setValueAtTime(0.3, now)
      gain.gain.exponentialRampToValueAtTime(0.01, now + 0.5)

      osc.start(now)
      osc.stop(now + 0.5)
    } else if (type === 'SELL') {
      // Gentle resolving chime (sine 784Hz -> 523Hz)
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.connect(gain)
      gain.connect(ctx.destination)

      const now = ctx.currentTime
      osc.frequency.setValueAtTime(783.99, now) // G5
      osc.frequency.setValueAtTime(523.25, now + 0.15) // C5

      gain.gain.setValueAtTime(0.2, now)
      gain.gain.exponentialRampToValueAtTime(0.01, now + 0.35)

      osc.start(now)
      osc.stop(now + 0.35)
    } else {
      // Crisp premium BUY chime (sine 587Hz -> 880Hz)
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.connect(gain)
      gain.connect(ctx.destination)

      const now = ctx.currentTime
      osc.frequency.setValueAtTime(587.33, now) // D5
      osc.frequency.setValueAtTime(880.0, now + 0.1) // A5

      gain.gain.setValueAtTime(0.25, now)
      gain.gain.exponentialRampToValueAtTime(0.01, now + 0.4)

      osc.start(now)
      osc.stop(now + 0.4)
    }
  } catch (err) {
    console.warn('Audio cue failed or blocked by browser autoplay policy:', err)
  }
}

export default function BuggyDashboard() {
  const [activeSignals, setActiveSignals] = useState([])
  const [completedSignals, setCompletedSignals] = useState([])
  const [isConnected, setIsConnected] = useState(false)
  const [lastHeartbeat, setLastHeartbeat] = useState(null)
  const [soundEnabled, setSoundEnabled] = useState(true)
  const [copiedId, setCopiedId] = useState(null)
  const [isLoadingInitial, setIsLoadingInitial] = useState(true)

  const eventSourceRef = useRef(null)
  const reconnectTimeoutRef = useRef(null)
  const soundEnabledRef = useRef(soundEnabled)

  useEffect(() => {
    soundEnabledRef.current = soundEnabled
  }, [soundEnabled])

  // =========================================================================
  // 1. Initial State Hydration via /api/buggy/overview
  // =========================================================================
  const fetchOverview = async () => {
    try {
      setIsLoadingInitial(true)
      const res = await fetch(`${BACKEND_URL}/api/buggy/overview`)
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

        const es = new EventSource(`${BACKEND_URL}/api/buggy/stream`)
        eventSourceRef.current = es

        es.onopen = () => {
          setIsConnected(true)
        }

        es.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data)

            if (data.type === 'HEARTBEAT') {
              setIsConnected(true)
              setLastHeartbeat(new Date().toLocaleTimeString())
              return
            }

            // A. ON BUY_SIGNAL: Push new "Active Trade Idea" card to the top
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

            // B. ON REVERSAL_ALERT: Flash background of corresponding card and show bold warning
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

            // C. ON SELL_SIGNAL: Move signal card to Recent Completed Signals list
            else if (data.type === 'SELL_SIGNAL') {
              setActiveSignals((prev) => prev.filter((s) => s.position_id !== data.position_id))
              setCompletedSignals((prev) => {
                const exists = prev.find((s) => s.position_id === data.position_id)
                if (exists) {
                  return prev.map((s) => (s.position_id === data.position_id ? { ...s, ...data } : s))
                }
                return [data, ...prev].slice(0, 30) // retain last 30
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
  // 3. Format and Copy Order Parameters for Broker Terminal
  // =========================================================================
  const handleCopyToBroker = (signal) => {
    try {
      const legsText = (signal.legs || [])
        .map(
          (l) =>
            `${l.action || 'BUY'} ${signal.ticker} ${l.strike} ${l.option_type || 'CE'} @ ₹${formatINR(
              l.fill_price || signal.recommended_entry_premium
            )}`
        )
        .join(' | ')

      const orderSummary = `[TRADING BUGGY REAL-MONEY SIGNAL]\nInstrument: ${signal.ticker}\nStrategy: ${
        signal.strategy_name
      }\nBias: ${signal.bias || 'DIRECTIONAL'}\nLegs: ${legsText || 'N/A'}\nRecommended Net Premium: ₹${formatINR(
        signal.recommended_entry_premium
      )}\nTarget: ₹${formatINR(signal.target)}\nStop-Loss: ₹${formatINR(signal.stop_loss)}\nTotal Qty: ${
        signal.total_quantity || signal.lot_size || 25
      } units`

      navigator.clipboard.writeText(orderSummary)
      setCopiedId(signal.position_id)
      setTimeout(() => setCopiedId(null), 2500)
    } catch (err) {
      console.error('Clipboard copy failed:', err)
    }
  }

  return (
    <div className="flex-1 flex flex-col overflow-y-auto bg-black text-slate-100 font-sans p-6 space-y-6">
      {/* =========================================================================
          HUD BANNER & CONTROLS (DARK MODE WITH NEON ACCENTS)
      ========================================================================= */}
      <div className="rounded-2xl border border-amber-500/40 bg-gradient-to-r from-slate-950 via-slate-900 to-amber-950/30 p-5 shadow-2xl shadow-amber-950/20 backdrop-blur-xl relative overflow-hidden">
        {/* Neon accent glow effect */}
        <div className="absolute top-0 right-0 w-96 h-96 bg-amber-500/5 rounded-full blur-3xl pointer-events-none" />

        <div className="flex flex-wrap items-center justify-between gap-4 relative z-10">
          <div className="space-y-1">
            <div className="flex items-center gap-2.5">
              <div className="p-2 rounded-xl bg-gradient-to-tr from-amber-500 to-yellow-400 text-black font-extrabold shadow-lg shadow-amber-500/30 animate-pulse">
                <Flame className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-xl font-black text-white tracking-wider flex items-center gap-2.5">
                  <span className="text-transparent bg-clip-text bg-gradient-to-r from-amber-400 via-yellow-200 to-amber-500">
                    TRADING BUGGY
                  </span>
                  <span className="text-[11px] font-mono font-bold px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/50 shadow-sm">
                    TRADE SIGNAL PROVIDER
                  </span>
                </h2>
                <p className="text-xs text-slate-400 font-mono mt-0.5">
                  Independent trade signal provider emitting actionable setups for manual execution in your Demat account (Angel One, Groww, Zerodha, etc.).
                </p>
              </div>
            </div>
          </div>

          {/* Quick HUD Metrics & Audio Toggles */}
          <div className="flex items-center gap-3">
            {/* Live SSE Status Pill */}
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-slate-950/80 border border-slate-800 text-xs font-mono">
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
              <span className={isConnected ? 'text-amber-300 font-bold' : 'text-rose-400'}>
                {isConnected ? 'LIVE SIGNAL FEED ACTIVE' : 'RECONNECTING...'}
              </span>
              {lastHeartbeat && (
                <span className="text-[10px] text-slate-500 border-l border-slate-800 pl-2">
                  {lastHeartbeat}
                </span>
              )}
            </div>

            {/* Audio Toggle */}
            <button
              onClick={() => setSoundEnabled(!soundEnabled)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs font-bold transition-all border ${
                soundEnabled
                  ? 'bg-amber-500/10 text-amber-300 border-amber-500/40 hover:bg-amber-500/20 shadow-sm shadow-amber-500/10'
                  : 'bg-slate-900 text-slate-400 border-slate-800 hover:text-slate-200'
              }`}
              title="Toggle browser notification chimes for Buy and Reversal alerts"
            >
              {soundEnabled ? <Volume2 className="w-3.5 h-3.5 text-amber-400" /> : <VolumeX className="w-3.5 h-3.5" />}
              <span>{soundEnabled ? 'Audio: ON' : 'Audio: MUTED'}</span>
            </button>

            {/* Test Audio Button */}
            <button
              onClick={() => playAudioCue('BUY', true)}
              className="px-2.5 py-1.5 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-[11px] font-mono text-slate-300 transition-colors"
              title="Test browser audio chime"
            >
              🔔 Test Chime
            </button>

            {/* Refresh Button */}
            <button
              onClick={fetchOverview}
              className="p-1.5 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-white transition-colors"
              title="Re-query initial Buggy state"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* Real Money Capital Warning Banner */}
        <div className="mt-4 p-3 rounded-xl border border-amber-500/20 bg-amber-950/20 text-xs text-amber-200/90 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 text-amber-400 flex-shrink-0" />
            <span>
              <strong>MANUAL DEMAT EXECUTION:</strong> Buggy provides trade ideas with exact strikes, target, and stop-loss. Click <strong>"Copy Order to Broker"</strong> to buy manually in your Demat account (Angel One, Groww, Zerodha, etc.). If a <strong>Reversal Alert</strong> triggers, manually square off to cut losses before the Stop-Loss is hit!
            </span>
          </div>
          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30 whitespace-nowrap">
            Manual Execution Only
          </span>
        </div>
      </div>

      {/* =========================================================================
          SECTION 1: ACTIVE REAL-MONEY TRADE IDEAS (PUSHED ON BUY_SIGNAL)
      ========================================================================= */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Zap className="w-5 h-5 text-amber-400" />
            <h3 className="text-base font-black text-white tracking-wide uppercase">
              Active Trade Ideas ({activeSignals.length})
            </h3>
          </div>
          <span className="text-xs font-mono text-slate-400">
            {activeSignals.length === 0 ? 'Radar Scanning for Opportunities' : 'Actionable Live Positions'}
          </span>
        </div>

        {activeSignals.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-800 bg-slate-950/60 p-12 text-center flex flex-col items-center justify-center space-y-3">
            <div className="p-3 rounded-2xl bg-amber-500/10 border border-amber-500/30 text-amber-400">
              <Radio className="w-8 h-8 animate-pulse" />
            </div>
            <h4 className="text-sm font-bold text-slate-200">Radar Scanning Live Market Feeds for Trade Setups</h4>
            <p className="text-xs text-slate-400 max-w-md font-mono">
              When a high-conviction opportunity is found, an actionable trade card with exact option strikes,
              entry premium, target, and stop-loss will appear here for manual buying in your Demat account.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-5">
            {activeSignals.map((signal) => {
              const isReversal = !!signal.isReversal
              const isBullish = signal.bias === 'BULLISH'
              const isBearish = signal.bias === 'BEARISH'
              const isCopied = copiedId === signal.position_id

              return (
                <div
                  key={signal.position_id}
                  className={`rounded-2xl border-2 transition-all p-6 shadow-2xl relative overflow-hidden ${
                    isReversal
                      ? 'border-rose-500 bg-rose-950/70 shadow-rose-950/80 animate-pulse ring-4 ring-rose-500/40'
                      : 'border-amber-500/50 bg-gradient-to-b from-slate-900/90 to-slate-950/90 shadow-amber-950/20 hover:border-amber-400'
                  }`}
                >
                  {/* REVERSAL FLASHING BANNER */}
                  {isReversal && (
                    <div className="mb-5 -mx-6 -mt-6 p-4 bg-gradient-to-r from-rose-600 to-red-700 text-white flex items-center justify-between shadow-lg">
                      <div className="flex items-center gap-3">
                        <AlertTriangle className="w-6 h-6 text-white animate-bounce flex-shrink-0" />
                        <div>
                          <div className="text-sm font-black tracking-wider uppercase">
                            ⚠️ REVERSAL DETECTED: Square Off Demat Position Early
                          </div>
                          <div className="text-xs font-mono text-rose-100">
                            {signal.reversalDetails?.detail ||
                              'Underlying market spot price moved against trade setup bias. Square off manually in your Demat account (Angel One, Groww, Zerodha) to cap losses.'}
                          </div>
                        </div>
                      </div>
                      <span className="px-3 py-1 rounded-lg bg-black/40 text-rose-200 font-mono text-xs font-bold border border-rose-400/50">
                        URGENT ACTION
                      </span>
                    </div>
                  )}

                  {/* Top Row: Symbol, Strategy, Bias Pill, and Timestamp */}
                  <div className="flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-slate-800/80">
                    <div className="flex items-center gap-3">
                      <span className="text-3xl font-black font-mono tracking-wider text-amber-300">
                        {signal.ticker}
                      </span>
                      <span className="px-3 py-1 rounded-xl text-xs font-bold bg-slate-800 text-slate-200 border border-slate-700 font-mono">
                        {signal.strategy_name}
                      </span>
                      <span
                        className={`flex items-center gap-1.5 px-3 py-1 rounded-xl text-xs font-black tracking-wider uppercase border font-mono ${
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
                      <span className="px-2 py-0.5 rounded bg-slate-950 text-slate-500 border border-slate-800">
                        Signal ID #{signal.position_id}
                      </span>
                    </div>
                  </div>

                  {/* Main Metrics Matrix */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 py-5 border-b border-slate-800/80">
                    {/* Recommended Entry Premium */}
                    <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800/80 space-y-1">
                      <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider block font-mono">
                        Recommended Entry Premium
                      </span>
                      <div className="text-2xl font-black font-mono text-white flex items-baseline gap-1.5">
                        <span>₹{formatINR(signal.recommended_entry_premium || Math.abs(signal.net_premium || 0))}</span>
                        <span className="text-xs font-bold text-amber-400">
                          {signal.trade_type === 'CREDIT' ? '(Credit)' : '(Debit)'}
                        </span>
                      </div>
                    </div>

                    {/* Target Profit */}
                    <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800/80 space-y-1">
                      <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider block font-mono">
                        Target Profit Exit
                      </span>
                      <div className="text-2xl font-black font-mono text-emerald-400">
                        +₹{formatINR(signal.target)}
                      </div>
                    </div>

                    {/* Hard Stop Loss */}
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
                          ({signal.total_quantity || signal.lot_size || 25} units)
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Option Strike Legs Breakdown */}
                  <div className="py-4 space-y-2">
                    <span className="text-xs font-bold text-slate-400 uppercase tracking-wider font-mono block">
                      Execution Option Legs:
                    </span>
                    <div className="flex flex-wrap gap-2">
                      {(signal.legs || []).map((leg, lIdx) => (
                        <div
                          key={lIdx}
                          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border font-mono text-xs ${
                            leg.action === 'BUY'
                              ? 'bg-emerald-950/50 border-emerald-500/50 text-emerald-200'
                              : 'bg-rose-950/50 border-rose-500/50 text-rose-200'
                          }`}
                        >
                          <span className="font-bold text-[10px] px-1 py-0.5 rounded bg-black/50">
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
                      <span className="text-amber-400/90 font-bold mr-2">Core Thesis:</span>
                      {signal.thesis}
                    </div>
                  )}

                  {/* BOTTOM ACTION BAR: COPY TO BROKER BUTTON */}
                  <div className="mt-5 pt-4 border-t border-slate-800/80 flex flex-wrap items-center justify-between gap-3">
                    <div className="text-xs text-slate-400 font-mono">
                      <span>Estimated Capital Required: </span>
                      <span className="text-white font-bold">₹{formatINR(signal.capital_allocated)}</span>
                    </div>

                    <button
                      onClick={() => handleCopyToBroker(signal)}
                      className={`flex items-center gap-2 px-5 py-2.5 rounded-xl font-mono text-xs font-bold transition-all shadow-lg active:scale-95 ${
                        isCopied
                          ? 'bg-emerald-500 text-black shadow-emerald-500/40 border border-emerald-400'
                          : 'bg-gradient-to-r from-amber-500 to-yellow-400 hover:from-amber-400 hover:to-yellow-300 text-black shadow-amber-500/25 border border-amber-400'
                      }`}
                    >
                      {isCopied ? <Check className="w-4 h-4 stroke-[3]" /> : <Copy className="w-4 h-4" />}
                      <span>{isCopied ? 'ORDER PARAMETERS COPIED! ✓' : 'COPY ORDER TO BROKER'}</span>
                    </button>
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
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Clock className="w-5 h-5 text-cyan-400" />
            <h3 className="text-base font-black text-white tracking-wide uppercase">
              Recent Completed Signals ({completedSignals.length})
            </h3>
          </div>
          <span className="text-xs font-mono text-slate-400">Exit Instructions & Execution History</span>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-900/40 overflow-hidden shadow-xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left font-mono text-xs border-collapse">
              <thead className="bg-slate-950 text-slate-400 border-b border-slate-800">
                <tr>
                  <th className="py-3 px-4">Exit Time</th>
                  <th className="py-3 px-4">Instrument</th>
                  <th className="py-3 px-4">Strategy</th>
                  <th className="py-3 px-4">Final Exit Instruction</th>
                  <th className="py-3 px-4">Exit Price</th>
                  <th className="py-3 px-4">Realized P&L</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {completedSignals.length === 0 ? (
                  <tr>
                    <td colSpan="6" className="text-center py-10 text-slate-500 font-sans text-xs">
                      No completed trade signals yet today.
                    </td>
                  </tr>
                ) : (
                  completedSignals.map((item, idx) => {
                    const isWin = (item.pnl || 0) >= 0
                    return (
                      <tr key={idx} className="hover:bg-slate-900/60 transition-colors">
                        <td className="py-3 px-4 text-slate-400 text-[11px]">
                          {item.exit_timestamp
                            ? new Date(item.exit_timestamp).toLocaleTimeString()
                            : '—'}
                        </td>
                        <td className="py-3 px-4 font-bold text-amber-300">{item.ticker}</td>
                        <td className="py-3 px-4 text-slate-300 font-sans">{item.strategy_name}</td>
                        <td className="py-3 px-4">
                          <div className="flex items-center gap-2">
                            <span
                              className={`px-2.5 py-0.5 rounded text-[11px] font-bold border ${
                                item.exit_reason?.includes('Target')
                                  ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
                                  : item.exit_reason?.includes('Stop')
                                  ? 'bg-rose-500/20 text-rose-300 border-rose-500/40'
                                  : 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                              }`}
                            >
                              {item.exit_reason || item.status}
                            </span>
                            <span className="text-[11px] text-slate-300 font-sans">
                              {item.instruction || 'Square off in your Demat account (Angel One/Groww/Zerodha).'}
                            </span>
                          </div>
                        </td>
                        <td className="py-3 px-4 text-slate-200">
                          {item.exit_price ? `₹${formatINR(item.exit_price)}` : '—'}
                        </td>
                        <td className="py-3 px-4 font-bold">
                          <span
                            className={`px-2 py-0.5 rounded text-xs ${
                              isWin
                                ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                                : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
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
