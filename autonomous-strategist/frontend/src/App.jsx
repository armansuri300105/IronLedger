import React, { useState, useEffect, useRef, Component } from 'react'
import {
  Terminal,
  TrendingUp,
  TrendingDown,
  ShieldCheck,
  ShieldAlert,
  Play,
  Square,
  RotateCcw,
  Zap,
  Activity,
  Code2,
  Layers,
  Database,
  Search,
  Scale,
  Calendar,
  BarChart3,
  Briefcase,
  Brain,
  Clock,
  ArrowUpRight,
  ArrowDownRight,
  XCircle,
  CheckCircle2,
  Wallet,
  RefreshCw,
  Sliders,
  AlertCircle,
  Info,
  Radio,
  Power,
  Check,
  Send,
  Eye,
  AlertTriangle,
  History,
  Target,
  Sparkles,
  ChevronRight,
  Flame,
} from 'lucide-react'

const BACKEND_URL = 'http://localhost:8000'

// Curated Market Instruments for Instant Agent Launch
const POPULAR_INSTRUMENTS = [
  { symbol: 'NIFTY', name: 'Nifty 50', category: 'INDEX', lotSize: 25 },
  { symbol: 'BANKNIFTY', name: 'Bank Nifty', category: 'INDEX', lotSize: 15 },
  { symbol: 'FINNIFTY', name: 'Nifty Financial Services', category: 'INDEX', lotSize: 25 },
  { symbol: 'MIDCPNIFTY', name: 'Nifty Midcap Select', category: 'INDEX', lotSize: 50 },
  { symbol: 'RELIANCE', name: 'Reliance Industries', category: 'STOCK', lotSize: 250 },
  { symbol: 'HDFCBANK', name: 'HDFC Bank', category: 'STOCK', lotSize: 550 },
  { symbol: 'INFY', name: 'Infosys Ltd', category: 'STOCK', lotSize: 400 },
  { symbol: 'TCS', name: 'Tata Consultancy Services', category: 'STOCK', lotSize: 175 },
  { symbol: 'ICICIBANK', name: 'ICICI Bank', category: 'STOCK', lotSize: 700 },
  { symbol: 'SBIN', name: 'State Bank of India', category: 'STOCK', lotSize: 750 },
  { symbol: 'TATAMOTORS', name: 'Tata Motors', category: 'STOCK', lotSize: 1425 },
]

// Safe Number Formatter (100% crash-proof against undefined, null, NaN)
const formatINR = (val, decimals = 2) => {
  const num = Number(val)
  if (isNaN(num) || !isFinite(num)) return '0.00'
  return num.toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

// Error Boundary to prevent white screen crashes
class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, errorInfo) {
    console.error('Unhandled UI Error caught by boundary:', error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center min-h-screen bg-slate-950 text-white p-6 text-center">
          <div className="p-4 rounded-2xl bg-rose-950/60 border border-rose-500/50 max-w-lg space-y-4 shadow-2xl">
            <AlertTriangle className="w-10 h-10 text-rose-400 mx-auto" />
            <h2 className="text-lg font-bold text-rose-200">Autonomous Strategist Recovered from Error</h2>
            <p className="text-xs text-slate-300 font-mono">
              {this.state.error?.message || 'A transient rendering issue occurred while refreshing.'}
            </p>
            <button
              onClick={() => {
                this.setState({ hasError: false, error: null })
                window.location.reload()
              }}
              className="px-4 py-2 bg-rose-600 hover:bg-rose-500 text-white rounded-xl text-xs font-bold transition-all shadow-lg shadow-rose-600/30"
            >
              Reload Dashboard
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

function MainApp() {
  // Navigation Tabs: 'mission_control' | 'portfolio' | 'memory'
  const [activeTab, setActiveTab] = useState('mission_control')

  // Selected Instrument & Instrument Filter Category
  const [selectedTicker, setSelectedTicker] = useState('NIFTY')
  const [instrumentFilter, setInstrumentFilter] = useState('ALL') // 'ALL' | 'INDEX' | 'STOCK'
  const [customTickerInput, setCustomTickerInput] = useState('')

  // Agent Launch & Execution State
  const [isAgentRunning, setIsAgentRunning] = useState(false)
  const [activePipelineStage, setActivePipelineStage] = useState(null) // 'data_engineer' | 'rag_researcher' | 'quant_analyst' | 'risk_manager' | 'order_execution_node' | 'completed'

  // Autopilot Engine Status
  const [autopilotStatus, setAutopilotStatus] = useState({
    active: true,
    market_open: true,
    market_session: 'Open',
    ist_time: '',
    bypass_active: true,
    watchlist: ['NIFTY', 'BANKNIFTY', 'RELIANCE', 'HDFCBANK', 'INFY'],
    max_positions: 3,
    open_positions_count: 0,
    cooldown_minutes: 30,
    cooldowns: {},
    currently_scanning: null,
    total_executions: 0,
    total_scans: 0,
    exit_logs: [],
  })
  const [isTogglingAutopilot, setIsTogglingAutopilot] = useState(false)

  // Live Activity Stream Events
  const [liveStreamEvents, setLiveStreamEvents] = useState([])
  const [autoScroll, setAutoScroll] = useState(true)

  // Market & Strategy Details
  const [marketData, setMarketData] = useState(null)
  const [quantProposal, setQuantProposal] = useState(null)
  const [riskApproved, setRiskApproved] = useState(false)
  const [finalTrade, setFinalTrade] = useState(null)

  // Portfolio & Virtual Ledger State
  const [portfolio, setPortfolio] = useState({
    virtual_balance: 1000000,
    margin_used: 0,
    available_cash: 1000000,
    realized_pnl: 0,
    unrealized_pnl: 0,
    net_worth: 1000000,
    win_rate_pct: 0,
    total_trades: 0,
    winning_trades: 0,
    open_positions: [],
  })
  const [history, setHistory] = useState([])
  const [isLoadingPortfolio, setIsLoadingPortfolio] = useState(false)

  // Memory & Lessons State
  const [lessonsMarkdown, setLessonsMarkdown] = useState('')
  const [isLoadingLessons, setIsLoadingLessons] = useState(false)

  // Toast Notification
  const [toast, setToast] = useState(null)

  const terminalEndRef = useRef(null)
  const liveEventSourceRef = useRef(null)
  const activeAgentStreamRef = useRef(null)
  const reconnectTimeoutRef = useRef(null)

  const showToast = (message, type = 'success') => {
    setToast({ message, type })
    setTimeout(() => setToast(null), 4500)
  }

  // Auto-scroll terminal
  useEffect(() => {
    if (autoScroll) {
      terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [liveStreamEvents, autoScroll])

  // =========================================================================
  // 1. Unified Background SSE Stream (/api/stream/live)
  // =========================================================================
  useEffect(() => {
    const connectLiveStream = () => {
      try {
        if (liveEventSourceRef.current) {
          liveEventSourceRef.current.close()
        }

        const es = new EventSource(`${BACKEND_URL}/api/stream/live`)
        liveEventSourceRef.current = es

        es.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data)
            setLiveStreamEvents((prev) => {
              const next = [...prev, data]
              return next.length > 120 ? next.slice(-120) : next
            })

            // Capture final trade / proposals
            if (data.final_trade && Object.keys(data.final_trade).length > 0) {
              setFinalTrade(data.final_trade)
              setQuantProposal(data.final_trade)
              setRiskApproved(true)
            }
            if (data.market_data && Object.keys(data.market_data).length > 0) {
              setMarketData(data.market_data)
            }

            // Highlighting auto-executions and exits
            if (data.type === 'AUTO_ORDER_FILLED' || data.badge_type === 'AUTO_BUY') {
              showToast(
                `AUTO-EXECUTION COMMITTED: Virtual ledger order filled for ${data.ticker || 'asset'}!`,
                'success'
              )
              fetchPortfolio()
            } else if (
              data.type === 'AUTO_EXIT' ||
              data.badge_type === 'TARGET_HIT' ||
              data.badge_type === 'SL_HIT' ||
              data.badge_type === 'EOD_SQUAREOFF'
            ) {
              showToast(data.message || `Auto-Exit executed: ${data.reason}`, 'info')
              fetchPortfolio()
              fetchLessons(data.ticker || selectedTicker)
            }
          } catch (err) {
            console.error('Error parsing live SSE event:', err)
          }
        }

        es.onerror = () => {
          es.close()
          clearTimeout(reconnectTimeoutRef.current)
          reconnectTimeoutRef.current = setTimeout(connectLiveStream, 5000)
        }
      } catch (err) {
        console.warn('Failed connecting live SSE stream:', err)
        clearTimeout(reconnectTimeoutRef.current)
        reconnectTimeoutRef.current = setTimeout(connectLiveStream, 6000)
      }
    }

    connectLiveStream()

    return () => {
      if (liveEventSourceRef.current) {
        liveEventSourceRef.current.close()
      }
      clearTimeout(reconnectTimeoutRef.current)
    }
  }, [])

  // =========================================================================
  // 2. Data Fetching & Polling
  // =========================================================================
  const fetchAutopilotStatus = async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/api/autopilot/status`)
      if (res.ok) {
        const data = await res.json()
        setAutopilotStatus((prev) => ({
          ...prev,
          ...data,
          exit_logs: Array.isArray(data.exit_logs) ? data.exit_logs : [],
          cooldowns: data.cooldowns || {},
        }))
      }
    } catch (err) {
      // Backend temporarily offline; ignore silently
    }
  }

  const fetchPortfolio = async () => {
    try {
      const [portRes, histRes] = await Promise.all([
        fetch(`${BACKEND_URL}/api/portfolio`).catch(() => null),
        fetch(`${BACKEND_URL}/api/positions/history`).catch(() => null),
      ])
      if (portRes && portRes.ok) {
        const portData = await portRes.json()
        setPortfolio((prev) => ({
          ...prev,
          ...portData,
          open_positions: Array.isArray(portData?.open_positions) ? portData.open_positions : [],
        }))
      }
      if (histRes && histRes.ok) {
        const histData = await histRes.json()
        setHistory(Array.isArray(histData) ? histData : [])
      }
    } catch (err) {
      // Ignore network errors gracefully
    }
  }

  const fetchLessons = async (symbol = selectedTicker) => {
    setIsLoadingLessons(true)
    try {
      const res = await fetch(`${BACKEND_URL}/api/lessons/${encodeURIComponent(symbol)}`)
      if (res.ok) {
        const data = await res.json()
        setLessonsMarkdown(data.lessons_markdown || '')
      }
    } catch (err) {
      // Ignore
    } finally {
      setIsLoadingLessons(false)
    }
  }

  useEffect(() => {
    fetchAutopilotStatus()
    fetchPortfolio()
    fetchLessons(selectedTicker)

    const interval = setInterval(() => {
      fetchAutopilotStatus()
      fetchPortfolio()
    }, 5000)

    return () => clearInterval(interval)
  }, [selectedTicker])

  useEffect(() => {
    if (activeTab === 'portfolio') {
      fetchPortfolio()
    } else if (activeTab === 'memory') {
      fetchLessons(selectedTicker)
    }
  }, [activeTab, selectedTicker])

  // =========================================================================
  // 3. START AGENT ON SELECTED INDEX / STOCK (USER REQUEST)
  // =========================================================================
  const handleStartAgent = (targetSymbol = selectedTicker) => {
    const symbolToRun = (targetSymbol || selectedTicker).trim().toUpperCase()
    if (!symbolToRun || isAgentRunning) return

    setIsAgentRunning(true)
    setActivePipelineStage('data_engineer')
    showToast(`Autonomous Agent launched on ${symbolToRun}! Ingesting live market data...`, 'info')

    // Close any previous single-agent stream
    if (activeAgentStreamRef.current) {
      activeAgentStreamRef.current.close()
    }

    const streamUrl = `${BACKEND_URL}/api/stream/${encodeURIComponent(symbolToRun)}`
    const es = new EventSource(streamUrl)
    activeAgentStreamRef.current = es

    es.onopen = () => {
      console.log(`[AGENT STREAM] Connected for ${symbolToRun}`)
    }

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)

        // Pipeline stage tracking
        if (data.node) {
          setActivePipelineStage(data.node)
        }

        // Add to live telemetry stream
        if (data.message) {
          setLiveStreamEvents((prev) => [
            ...prev,
            {
              type: 'AGENT_STEP',
              ticker: symbolToRun,
              node: data.node,
              agent: data.message.agent || data.node,
              stage: data.message.stage || '',
              text: data.message.text || '',
              badge_type: data.message.badge_type || 'INFO',
              timestamp: data.message.timestamp,
            },
          ])
        }

        if (data.market_data && Object.keys(data.market_data).length > 0) {
          setMarketData(data.market_data)
        }
        if (data.quant_proposal && Object.keys(data.quant_proposal).length > 0) {
          setQuantProposal(data.quant_proposal)
        }
        if (typeof data.risk_approved === 'boolean') {
          setRiskApproved(data.risk_approved)
        }

        // Completion on order execution or completion
        if (data.node === 'order_execution_node' || data.final_trade) {
          if (data.final_trade && Object.keys(data.final_trade).length > 0) {
            setFinalTrade(data.final_trade)
          }

          if (data.execution_details && data.execution_details.success) {
            showToast(
              `Order Executed on Ledger: ${data.final_trade?.strategy_name || 'Setup'} filled!`,
              'success'
            )
            fetchPortfolio()
          } else if (data.risk_approved === false) {
            showToast(`Proposal rejected by Risk Manager for ${symbolToRun}.`, 'error')
          }

          setActivePipelineStage('completed')
          setIsAgentRunning(false)
          es.close()
        }
      } catch (err) {
        console.error('Error handling agent stream chunk:', err)
      }
    }

    es.onerror = () => {
      console.warn(`[AGENT STREAM] Stream ended or encountered network error for ${symbolToRun}`)
      es.close()
      setIsAgentRunning(false)
      setActivePipelineStage(null)
    }
  }

  const handleStopAgent = () => {
    if (activeAgentStreamRef.current) {
      activeAgentStreamRef.current.close()
      activeAgentStreamRef.current = null
    }
    setIsAgentRunning(false)
    setActivePipelineStage(null)
    showToast('Agent execution halted by user.', 'info')
  }

  // Autopilot Emergency Kill Switch
  const handleToggleAutopilot = async () => {
    setIsTogglingAutopilot(true)
    try {
      const res = await fetch(`${BACKEND_URL}/api/autopilot/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active: !autopilotStatus.active }),
      })
      const data = await res.json()
      if (res.ok) {
        setAutopilotStatus((prev) => ({ ...prev, active: data.active }))
        showToast(
          `Autopilot is now ${data.active ? 'ACTIVE (SCANNING)' : 'PAUSED (STANDBY)'}!`,
          data.active ? 'success' : 'error'
        )
      }
    } catch (err) {
      showToast('Network error toggling autopilot.', 'error')
    } finally {
      setIsTogglingAutopilot(false)
    }
  }

  // Manual Position Close
  const handleClosePosition = async (positionId) => {
    try {
      const res = await fetch(`${BACKEND_URL}/api/positions/${positionId}/close`, {
        method: 'POST',
      })
      const data = await res.json()
      if (res.ok && data.success) {
        showToast(
          `Position #${positionId} closed with PnL of ₹${formatINR(data.pnl)}! Reflection saved.`,
          'info'
        )
        fetchPortfolio()
        fetchLessons(selectedTicker)
      } else {
        showToast(data.detail || 'Failed closing position.', 'error')
      }
    } catch (err) {
      showToast('Network error closing position.', 'error')
    }
  }

  // Filtered Instruments List
  const filteredInstruments = POPULAR_INSTRUMENTS.filter((inst) => {
    if (instrumentFilter === 'ALL') return true
    return inst.category === instrumentFilter
  })

  // Combined P&L calculations
  const totalNetPnl = (portfolio.realized_pnl || 0) + (portfolio.unrealized_pnl || 0)
  const returnOnCapital = ((totalNetPnl / 1000000) * 100).toFixed(2)

  // Pipeline Stepper Stages
  const PIPELINE_STEPS = [
    { id: 'data_engineer', name: 'Data Engineer', desc: 'NSE Greeks & PCR' },
    { id: 'rag_researcher', name: 'RAG Intelligence', desc: 'News & ChromaDB' },
    { id: 'quant_analyst', name: 'Quant Analyst', desc: 'Gemini Strategy' },
    { id: 'risk_manager', name: 'Risk Manager', desc: 'Desk Hard Checks' },
    { id: 'order_execution_node', name: 'Auto-Execution', desc: 'Virtual Ledger' },
  ]

  // Event Card Renderer
  const renderTelemetryItem = (ev, idx) => {
    const badgeType = ev.badge_type || 'INFO'
    const timestamp = ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : ''

    if (badgeType === 'AUTO_BUY' || ev.type === 'AUTO_ORDER_FILLED') {
      return (
        <div
          key={idx}
          className="rounded-xl border-2 border-emerald-500/80 bg-emerald-950/40 p-4 shadow-lg shadow-emerald-950/50 space-y-2 transition-all animate-in fade-in"
        >
          <div className="flex items-center justify-between border-b border-emerald-500/30 pb-2">
            <span className="flex items-center gap-2 text-xs font-bold text-emerald-300 uppercase tracking-wide">
              <span className="relative flex h-2.5 w-2.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500"></span>
              </span>
              <span>AUTO-EXECUTION COMMITTED</span>
            </span>
            <span className="text-[10px] font-mono text-emerald-400/80">{timestamp}</span>
          </div>
          <div className="text-xs text-emerald-100 font-semibold leading-relaxed">
            {ev.text || ev.message || `Virtual order placed automatically on paper ledger for ${ev.ticker}.`}
          </div>
          {ev.strategy_name && (
            <div className="flex items-center gap-2 text-[11px] font-mono text-emerald-300">
              <span className="bg-emerald-900/60 px-2 py-0.5 rounded border border-emerald-700/60 font-bold">
                {ev.strategy_name}
              </span>
              {ev.fill_price !== undefined && (
                <span className="text-emerald-400 font-bold">Fill: ₹{formatINR(ev.fill_price)}</span>
              )}
              {ev.position_id && <span className="text-emerald-400/80">Pos ID #{ev.position_id}</span>}
            </div>
          )}
        </div>
      )
    }

    if (badgeType === 'RISK_REJECTED' || ev.type === 'RISK_REJECTED') {
      return (
        <div
          key={idx}
          className="rounded-xl border-2 border-amber-500/80 bg-amber-950/40 p-4 shadow-lg shadow-amber-950/50 space-y-2 transition-all animate-in fade-in"
        >
          <div className="flex items-center justify-between border-b border-amber-500/30 pb-2">
            <span className="flex items-center gap-2 text-xs font-bold text-amber-300 uppercase tracking-wide">
              <ShieldAlert className="w-4 h-4 text-amber-400" />
              <span>RISK CRITIQUE: PROPOSAL REJECTED</span>
            </span>
            <span className="text-[10px] font-mono text-amber-400/80">{timestamp}</span>
          </div>
          <div className="text-xs text-amber-100/90 font-mono leading-relaxed">
            {ev.text || ev.message || ev.critique || 'Risk Manager rejected proposal. Desk constraints breached.'}
          </div>
        </div>
      )
    }

    if (badgeType === 'POSTMORTEM_SAVED' || ev.type === 'POSTMORTEM_SAVED') {
      return (
        <div
          key={idx}
          className="rounded-xl border-2 border-cyan-500/80 bg-cyan-950/40 p-4 shadow-lg shadow-cyan-950/50 space-y-2 transition-all animate-in fade-in"
        >
          <div className="flex items-center justify-between border-b border-cyan-500/30 pb-2">
            <span className="flex items-center gap-2 text-xs font-bold text-cyan-300 uppercase tracking-wide">
              <Brain className="w-4 h-4 text-cyan-400" />
              <span>CHROMADB REFLECTION LEARNED</span>
            </span>
            <span className="text-[10px] font-mono text-cyan-400/80">{timestamp}</span>
          </div>
          <div className="text-xs text-cyan-100 font-mono leading-relaxed">
            {ev.text || ev.message || 'Post-mortem learning agent stored actionable lesson in ChromaDB.'}
          </div>
        </div>
      )
    }

    if (
      badgeType === 'TARGET_HIT' ||
      badgeType === 'SL_HIT' ||
      badgeType === 'EOD_SQUAREOFF' ||
      ev.type === 'AUTO_EXIT'
    ) {
      const isTarget = badgeType === 'TARGET_HIT'
      return (
        <div
          key={idx}
          className={`rounded-xl border-2 p-4 shadow-lg space-y-2 transition-all animate-in fade-in ${
            isTarget
              ? 'border-emerald-400/80 bg-emerald-950/30 shadow-emerald-950/50'
              : 'border-purple-500/80 bg-purple-950/30 shadow-purple-950/50'
          }`}
        >
          <div className="flex items-center justify-between border-b border-slate-800 pb-2">
            <span
              className={`flex items-center gap-2 text-xs font-bold uppercase tracking-wide ${
                isTarget ? 'text-emerald-300' : 'text-purple-300'
              }`}
            >
              <Target className="w-4 h-4" />
              <span>{badgeType === 'EOD_SQUAREOFF' ? '15:15 IST AUTO-SQUAREOFF' : 'AUTO-EXIT SAFEGUARD HIT'}</span>
            </span>
            <span className="text-[10px] font-mono text-slate-400">{timestamp}</span>
          </div>
          <div className="text-xs font-semibold text-slate-100">{ev.reason || ev.message}</div>
          {ev.pnl !== undefined && (
            <div className="text-xs font-mono font-bold">
              Final Realized P&L:{' '}
              <span className={ev.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                {ev.pnl >= 0 ? '+' : ''}₹{formatINR(ev.pnl)}
              </span>
            </div>
          )}
        </div>
      )
    }

    return (
      <div
        key={idx}
        className="rounded-lg border border-slate-800/80 bg-slate-900/60 p-3 space-y-1 hover:border-slate-700 transition-colors"
      >
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-1.5 text-[11px] font-bold text-slate-300">
            <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono text-[10px]">
              {ev.ticker || 'SYSTEM'}
            </span>
            <span>{ev.agent || ev.node || 'Agent Reasoning'}</span>
            {ev.stage && <span className="text-slate-500 font-normal">({ev.stage})</span>}
          </span>
          <span className="text-[10px] font-mono text-slate-500">{timestamp}</span>
        </div>
        <div className="text-xs font-mono text-slate-300 leading-relaxed whitespace-pre-wrap">
          {ev.text || ev.message || JSON.stringify(ev)}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-slate-950 font-sans text-slate-100">
      {/* Toast Notification */}
      {toast && (
        <div
          className={`fixed top-20 right-6 z-50 flex items-center gap-2.5 px-4 py-3 rounded-xl shadow-2xl border text-xs font-semibold backdrop-blur-md transition-all animate-in fade-in slide-in-from-top-3 ${
            toast.type === 'success'
              ? 'bg-emerald-950/90 border-emerald-500/50 text-emerald-200 shadow-emerald-950/60'
              : toast.type === 'error'
              ? 'bg-rose-950/90 border-rose-500/50 text-rose-200 shadow-rose-950/60'
              : 'bg-cyan-950/90 border-cyan-500/50 text-cyan-200 shadow-cyan-950/60'
          }`}
        >
          {toast.type === 'success' ? (
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          ) : toast.type === 'error' ? (
            <AlertCircle className="w-4 h-4 text-rose-400" />
          ) : (
            <Info className="w-4 h-4 text-cyan-400" />
          )}
          <span>{toast.message}</span>
        </div>
      )}

      {/* =========================================================================
          TOP AUTOPILOT STATUS & TELEMETRY BAR
      ========================================================================= */}
      <header className="h-16 flex-shrink-0 border-b border-slate-800 bg-slate-900/90 backdrop-blur-md px-6 flex items-center justify-between z-30">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-xl bg-gradient-to-tr from-emerald-500 to-cyan-500 shadow-lg shadow-emerald-500/20">
              <Zap className="w-5 h-5 text-slate-950 font-bold" />
            </div>
            <div>
              <h1 className="font-bold text-sm tracking-tight text-white flex items-center gap-2">
                <span>Autonomous Strategist</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">
                  v2.2
                </span>
              </h1>
              <p className="text-[11px] text-slate-400">Equity & Options Multi-Agent Autopilot</p>
            </div>
          </div>

          <div className="h-5 w-px bg-slate-800" />

          {/* Autopilot Status Badge */}
          <div className="flex items-center gap-2">
            {autopilotStatus.active ? (
              <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/40 text-emerald-300 text-xs font-bold tracking-wide shadow-sm shadow-emerald-500/20">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                </span>
                <span>AUTOPILOT: ACTIVE (SCANNING)</span>
              </div>
            ) : (
              <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-rose-500/10 border border-rose-500/40 text-rose-300 text-xs font-bold tracking-wide shadow-sm shadow-rose-500/20">
                <span className="inline-flex rounded-full h-2 w-2 bg-rose-500"></span>
                <span>AUTOPILOT: PAUSED</span>
              </div>
            )}
          </div>
        </div>

        {/* Market Session, Risk Cap, and Kill Switch */}
        <div className="flex items-center gap-3 text-xs font-mono">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-950/80 border border-slate-800 text-slate-300">
            <Clock className="w-3.5 h-3.5 text-cyan-400" />
            <span>{autopilotStatus.ist_time || 'IST Session'}</span>
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                autopilotStatus.market_open
                  ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                  : 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
              }`}
            >
              {autopilotStatus.market_session || (autopilotStatus.market_open ? 'Open' : 'Closed')}
            </span>
          </div>

          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-950/80 border border-slate-800">
            <ShieldAlert className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-slate-400">Risk Cap:</span>
            <span
              className={`font-bold ${
                (autopilotStatus.open_positions_count || 0) >= (autopilotStatus.max_positions || 3)
                  ? 'text-rose-400'
                  : 'text-emerald-400'
              }`}
            >
              {autopilotStatus.open_positions_count || 0} / {autopilotStatus.max_positions || 3} Active
            </span>
          </div>

          <button
            onClick={handleToggleAutopilot}
            disabled={isTogglingAutopilot}
            className={`flex items-center gap-2 px-4 py-2 rounded-xl font-sans font-bold text-xs transition-all shadow-md active:scale-95 ${
              autopilotStatus.active
                ? 'bg-rose-600 hover:bg-rose-500 text-white shadow-rose-600/25 border border-rose-500'
                : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-600/25 border border-emerald-500'
            }`}
          >
            <Power className="w-3.5 h-3.5" />
            <span>{autopilotStatus.active ? 'KILL SWITCH (PAUSE)' : 'RESUME AUTOPILOT'}</span>
          </button>
        </div>
      </header>

      {/* Main App Workspace */}
      <div className="flex flex-1 overflow-hidden">
        {/* =========================================================================
            LEFT SIDEBAR: Navigation Tabs & Watchlist Radar
        ========================================================================= */}
        <aside className="w-72 flex-shrink-0 flex flex-col border-r border-slate-800 bg-slate-900/70 p-4 backdrop-blur-md">
          {/* Navigation Tabs */}
          <div className="grid grid-cols-3 gap-1 p-1 bg-slate-950/80 rounded-xl border border-slate-800 text-[11px] font-semibold">
            <button
              onClick={() => setActiveTab('mission_control')}
              className={`flex flex-col items-center gap-1 py-2 px-1 rounded-lg transition-all ${
                activeTab === 'mission_control'
                  ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
              }`}
            >
              <Activity className="w-4 h-4" />
              <span>Autopilot</span>
            </button>
            <button
              onClick={() => setActiveTab('portfolio')}
              className={`flex flex-col items-center gap-1 py-2 px-1 rounded-lg transition-all ${
                activeTab === 'portfolio'
                  ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
              }`}
            >
              <Briefcase className="w-4 h-4" />
              <span>Portfolio</span>
            </button>
            <button
              onClick={() => setActiveTab('memory')}
              className={`flex flex-col items-center gap-1 py-2 px-1 rounded-lg transition-all ${
                activeTab === 'memory'
                  ? 'bg-purple-500/20 text-purple-300 border border-purple-500/40 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
              }`}
            >
              <Brain className="w-4 h-4" />
              <span>Memory</span>
            </button>
          </div>

          {/* Watchlist Quick Launch & Radar */}
          <div className="mt-5 flex-1 flex flex-col space-y-3 overflow-hidden">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                <Radio className="w-3.5 h-3.5 text-emerald-400" />
                <span>Watchlist Radar</span>
              </span>
              <span className="text-[10px] text-slate-500 font-mono">5 Symbols</span>
            </div>

            <div className="space-y-2 overflow-y-auto pr-1">
              {(autopilotStatus.watchlist || ['NIFTY', 'BANKNIFTY', 'RELIANCE', 'HDFCBANK', 'INFY']).map(
                (sym) => {
                  const isScanning = autopilotStatus.currently_scanning === sym
                  const isSelected = selectedTicker === sym
                  const cooldownInfo = autopilotStatus.cooldowns?.[sym]
                  const inCooldown = cooldownInfo && !cooldownInfo.eligible

                  return (
                    <button
                      key={sym}
                      onClick={() => {
                        setSelectedTicker(sym)
                        fetchLessons(sym)
                      }}
                      className={`w-full flex items-center justify-between p-3 rounded-xl border text-xs font-mono transition-all text-left ${
                        isSelected
                          ? 'border-emerald-500/60 bg-emerald-950/30 text-white shadow-sm'
                          : 'border-slate-800/80 bg-slate-950/50 text-slate-300 hover:border-slate-700'
                      }`}
                    >
                      <div className="flex items-center gap-2.5">
                        <div
                          className={`w-2 h-2 rounded-full ${
                            isScanning
                              ? 'bg-emerald-400 animate-ping'
                              : inCooldown
                              ? 'bg-amber-400'
                              : 'bg-emerald-500'
                          }`}
                        />
                        <span className="font-bold text-white tracking-wide">{sym}</span>
                      </div>

                      <div className="flex items-center gap-2">
                        {isScanning ? (
                          <span className="text-[10px] font-bold text-emerald-400 animate-pulse bg-emerald-950/80 px-2 py-0.5 rounded border border-emerald-500/40">
                            SCANNING
                          </span>
                        ) : inCooldown ? (
                          <span className="text-[10px] text-amber-400/90 font-mono bg-amber-950/50 px-1.5 py-0.5 rounded border border-amber-800/40">
                            {Math.ceil((cooldownInfo?.cooldown_remaining_sec || 0) / 60)}m cool
                          </span>
                        ) : (
                          <span className="text-[10px] text-emerald-400/90 bg-emerald-950/40 px-1.5 py-0.5 rounded border border-emerald-900/60">
                            Ready
                          </span>
                        )}
                      </div>
                    </button>
                  )
                }
              )}
            </div>
          </div>

          {/* Sidebar Stats Summary */}
          <div className="mt-auto pt-4 border-t border-slate-800 space-y-2 text-xs font-mono">
            <div className="flex justify-between text-slate-400 text-[11px]">
              <span>Completed Scans:</span>
              <span className="text-white font-bold">{autopilotStatus.total_scans || 0}</span>
            </div>
            <div className="flex justify-between text-slate-400 text-[11px]">
              <span>Auto Executions:</span>
              <span className="text-emerald-400 font-bold">{autopilotStatus.total_executions || 0}</span>
            </div>
            <div className="flex justify-between text-slate-400 text-[11px]">
              <span>Base Virtual Fund:</span>
              <span className="text-cyan-400 font-bold">₹10,00,000</span>
            </div>
          </div>
        </aside>

        {/* =========================================================================
            MISSION CONTROL: AGENT LAUNCHER & TELEMETRY STREAM (USER REQUEST)
        ========================================================================= */}
        {activeTab === 'mission_control' && (
          <main className="flex-1 flex flex-col overflow-hidden bg-slate-950">
            {/* 1. AGENT LAUNCH CONTROL CENTER (USER REQUEST: BUTTON & OPTION TO START AGENT) */}
            <div className="p-4 border-b border-slate-800 bg-slate-900/60 backdrop-blur-md space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                {/* Left: Category Selector & Instrument Pills */}
                <div className="flex items-center gap-2">
                  <span className="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center gap-1.5 mr-2">
                    <Sparkles className="w-4 h-4 text-emerald-400" />
                    <span>Select Index / Stock:</span>
                  </span>

                  {/* Filter Tabs */}
                  <div className="flex items-center p-0.5 rounded-lg bg-slate-950 border border-slate-800 text-[11px] font-semibold">
                    <button
                      onClick={() => setInstrumentFilter('ALL')}
                      className={`px-2 py-1 rounded transition-all ${
                        instrumentFilter === 'ALL'
                          ? 'bg-emerald-500/20 text-emerald-300 font-bold'
                          : 'text-slate-400 hover:text-white'
                      }`}
                    >
                      All (F&O)
                    </button>
                    <button
                      onClick={() => setInstrumentFilter('INDEX')}
                      className={`px-2 py-1 rounded transition-all ${
                        instrumentFilter === 'INDEX'
                          ? 'bg-emerald-500/20 text-emerald-300 font-bold'
                          : 'text-slate-400 hover:text-white'
                      }`}
                    >
                      Indices
                    </button>
                    <button
                      onClick={() => setInstrumentFilter('STOCK')}
                      className={`px-2 py-1 rounded transition-all ${
                        instrumentFilter === 'STOCK'
                          ? 'bg-emerald-500/20 text-emerald-300 font-bold'
                          : 'text-slate-400 hover:text-white'
                      }`}
                    >
                      Equities
                    </button>
                  </div>
                </div>

                {/* Right: THE PRIMARY "START AGENT" BUTTON */}
                <div className="flex items-center gap-3">
                  {/* Custom Ticker Input */}
                  <div className="relative">
                    <input
                      type="text"
                      placeholder="Custom NSE Ticker..."
                      value={customTickerInput}
                      onChange={(e) => setCustomTickerInput(e.target.value.toUpperCase())}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && customTickerInput.trim()) {
                          setSelectedTicker(customTickerInput.trim().toUpperCase())
                          handleStartAgent(customTickerInput.trim().toUpperCase())
                        }
                      }}
                      className="px-3 py-2 w-44 rounded-xl bg-slate-950 border border-slate-800 text-xs font-mono text-white placeholder-slate-500 focus:outline-none focus:border-emerald-500/60"
                    />
                  </div>

                  {/* PROMINENT START AGENT BUTTON */}
                  {isAgentRunning ? (
                    <button
                      onClick={handleStopAgent}
                      className="flex items-center gap-2 px-5 py-2.5 rounded-xl font-bold text-xs bg-rose-600 hover:bg-rose-500 text-white shadow-lg shadow-rose-600/30 transition-all active:scale-95 border border-rose-400"
                    >
                      <Square className="w-4 h-4 fill-current" />
                      <span>ABORT AGENT ({selectedTicker})</span>
                    </button>
                  ) : (
                    <button
                      onClick={() => handleStartAgent(selectedTicker)}
                      className="flex items-center gap-2 px-6 py-2.5 rounded-xl font-bold text-xs bg-gradient-to-r from-emerald-500 via-teal-500 to-cyan-500 hover:from-emerald-400 hover:to-cyan-400 text-slate-950 shadow-xl shadow-emerald-500/25 transition-all active:scale-95 border border-emerald-300"
                    >
                      <Play className="w-4 h-4 fill-current text-slate-950 font-black" />
                      <span>START AGENT ON {selectedTicker}</span>
                    </button>
                  )}
                </div>
              </div>

              {/* Instrument Selection Pills */}
              <div className="flex items-center gap-2 overflow-x-auto pb-1 pt-0.5 no-scrollbar">
                {filteredInstruments.map((inst) => {
                  const isSelected = selectedTicker === inst.symbol
                  return (
                    <button
                      key={inst.symbol}
                      onClick={() => {
                        setSelectedTicker(inst.symbol)
                        fetchLessons(inst.symbol)
                      }}
                      className={`flex items-center gap-2 px-3 py-1.5 rounded-xl border text-xs font-mono whitespace-nowrap transition-all ${
                        isSelected
                          ? 'border-emerald-500 bg-emerald-500/20 text-emerald-200 font-bold shadow-md shadow-emerald-950'
                          : 'border-slate-800 bg-slate-950/70 text-slate-400 hover:border-slate-700 hover:text-slate-200'
                      }`}
                    >
                      <span>{inst.symbol}</span>
                      <span className="text-[10px] px-1.5 py-0.2 rounded bg-slate-800 text-slate-400">
                        {inst.category === 'INDEX' ? 'Index' : 'Stock'}
                      </span>
                    </button>
                  )
                })}
              </div>

              {/* LIVE MULTI-AGENT PIPELINE STEPPER TRACKER */}
              {isAgentRunning && (
                <div className="pt-2 border-t border-slate-800/80">
                  <div className="flex items-center justify-between text-xs font-mono">
                    {PIPELINE_STEPS.map((st, idx) => {
                      const isActive = activePipelineStage === st.id
                      return (
                        <div
                          key={st.id}
                          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border transition-all ${
                            isActive
                              ? 'border-emerald-500/80 bg-emerald-950/60 text-emerald-300 font-bold shadow-sm shadow-emerald-500/20 animate-pulse'
                              : 'border-slate-800/80 bg-slate-950/60 text-slate-400'
                          }`}
                        >
                          <span className="w-4 h-4 rounded-full bg-slate-800 flex items-center justify-center text-[10px] text-white">
                            {idx + 1}
                          </span>
                          <span>{st.name}</span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>

            {/* 2. AUTONOMOUS HEALTH & P&L TELEMETRY CARDS */}
            <div className="p-4 border-b border-slate-800 bg-slate-900/40 grid grid-cols-4 gap-4">
              <div className="p-3.5 rounded-xl border border-slate-800 bg-slate-950/70 backdrop-blur-md">
                <span className="text-[11px] font-semibold uppercase text-slate-400 flex items-center justify-between">
                  <span>Virtual Capital</span>
                  <Wallet className="w-4 h-4 text-emerald-400" />
                </span>
                <span className="text-xl font-bold font-mono text-white block mt-1.5">
                  ₹{formatINR(portfolio.virtual_balance)}
                </span>
                <div className="text-[10px] text-slate-400 mt-1 flex justify-between">
                  <span>Free: ₹{formatINR(portfolio.available_cash)}</span>
                  <span className="text-cyan-400">Margin: ₹{formatINR(portfolio.margin_used)}</span>
                </div>
              </div>

              <div className="p-3.5 rounded-xl border border-slate-800 bg-slate-950/70 backdrop-blur-md">
                <span className="text-[11px] font-semibold uppercase text-slate-400 flex items-center justify-between">
                  <span>Net P&L (Realized + MTM)</span>
                  {totalNetPnl >= 0 ? (
                    <TrendingUp className="w-4 h-4 text-emerald-400" />
                  ) : (
                    <TrendingDown className="w-4 h-4 text-rose-400" />
                  )}
                </span>
                <span
                  className={`text-xl font-bold font-mono block mt-1.5 ${
                    totalNetPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                  }`}
                >
                  {totalNetPnl >= 0 ? '+' : ''}₹{formatINR(totalNetPnl)}
                </span>
                <div className="text-[10px] text-slate-400 mt-1 flex justify-between font-mono">
                  <span>Realized: ₹{formatINR(portfolio.realized_pnl)}</span>
                  <span className={totalNetPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                    {returnOnCapital}% Return
                  </span>
                </div>
              </div>

              <div className="p-3.5 rounded-xl border border-slate-800 bg-slate-950/70 backdrop-blur-md">
                <span className="text-[11px] font-semibold uppercase text-slate-400 flex items-center justify-between">
                  <span>Desk Win Rate</span>
                  <Activity className="w-4 h-4 text-amber-400" />
                </span>
                <span className="text-xl font-bold font-mono text-amber-400 block mt-1.5">
                  {portfolio.win_rate_pct || 0}%
                </span>
                <span className="text-[10px] text-slate-400 block mt-1">
                  {portfolio.winning_trades || 0} / {portfolio.total_trades || 0} Closed Trades
                </span>
              </div>

              <div className="p-3.5 rounded-xl border border-slate-800 bg-slate-950/70 backdrop-blur-md">
                <span className="text-[11px] font-semibold uppercase text-slate-400 flex items-center justify-between">
                  <span>Auto-Exit Safeguards</span>
                  <ShieldCheck className="w-4 h-4 text-purple-400" />
                </span>
                <span className="text-sm font-bold font-mono text-purple-300 block mt-1.5">
                  15:15 IST Square-off
                </span>
                <span className="text-[10px] text-slate-400 block mt-1">
                  Strict SL Cap: ₹15,000 | 15s MTM Check
                </span>
              </div>
            </div>

            {/* 3. SPLIT SCREEN: LIVE ACTIVITY STREAM + AUTO-EXIT LOG / ACTIVE TRADE */}
            <div className="flex-1 flex overflow-hidden">
              {/* LEFT: Live Autonomous Activity Stream */}
              <div className="flex-1 flex flex-col border-r border-slate-800 bg-slate-950 p-4 overflow-hidden">
                <div className="flex items-center justify-between pb-3 border-b border-slate-800">
                  <div className="flex items-center gap-2">
                    <Terminal className="w-4 h-4 text-emerald-400" />
                    <h2 className="text-xs font-bold text-white uppercase tracking-wider">
                      Live Multi-Agent Activity Stream
                    </h2>
                  </div>
                  <div className="flex items-center gap-3">
                    <label className="flex items-center gap-1.5 text-[11px] text-slate-400 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={autoScroll}
                        onChange={(e) => setAutoScroll(e.target.checked)}
                        className="rounded border-slate-700 bg-slate-800 text-emerald-500 focus:ring-0"
                      />
                      <span>Auto-scroll</span>
                    </label>
                    <button
                      onClick={() => setLiveStreamEvents([])}
                      className="text-[10px] text-slate-500 hover:text-slate-300 font-mono"
                    >
                      Clear
                    </button>
                  </div>
                </div>

                <div className="flex-1 overflow-y-auto space-y-2.5 pt-3 pr-1">
                  {liveStreamEvents.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center text-slate-600 text-xs font-mono space-y-3">
                      <Activity className="w-8 h-8 animate-pulse text-emerald-500/40" />
                      <span>Ready. Select any Index or Stock above and click "START AGENT".</span>
                    </div>
                  ) : (
                    liveStreamEvents.map((ev, idx) => renderTelemetryItem(ev, idx))
                  )}
                  <div ref={terminalEndRef} />
                </div>
              </div>

              {/* RIGHT: Auto-Exit Log & Active Strategy Snapshot */}
              <div className="w-96 flex-shrink-0 flex flex-col bg-slate-900/30 p-4 overflow-y-auto space-y-4">
                {/* AUTO-EXIT LOG */}
                <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 space-y-3">
                  <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                    <span className="text-xs font-bold text-white flex items-center gap-1.5">
                      <Target className="w-4 h-4 text-purple-400" />
                      <span>Auto-Exit Log</span>
                    </span>
                    <span className="text-[10px] text-slate-500 font-mono">
                      {(autopilotStatus.exit_logs && autopilotStatus.exit_logs.length) || 0} Triggers
                    </span>
                  </div>

                  <div className="space-y-2 max-h-52 overflow-y-auto pr-1">
                    {!autopilotStatus.exit_logs || autopilotStatus.exit_logs.length === 0 ? (
                      <p className="text-xs text-slate-500 italic py-2 text-center">
                        No exit triggers recorded yet in current session.
                      </p>
                    ) : (
                      autopilotStatus.exit_logs.map((exit, idx) => (
                        <div
                          key={idx}
                          className="p-2.5 rounded-lg border border-slate-800 bg-slate-950/80 text-xs font-mono space-y-1"
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-bold text-white">{exit.ticker}</span>
                            <span
                              className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                (exit.pnl || 0) >= 0
                                  ? 'bg-emerald-500/20 text-emerald-400'
                                  : 'bg-rose-500/20 text-rose-400'
                              }`}
                            >
                              {(exit.pnl || 0) >= 0 ? '+' : ''}₹{formatINR(exit.pnl || 0)}
                            </span>
                          </div>
                          <div className="text-[11px] text-slate-300">{exit.reason}</div>
                        </div>
                      ))
                    )}
                  </div>
                </div>

                {/* CURRENT ACTIVE PROPOSAL CARD */}
                {finalTrade ? (
                  <div className="rounded-xl border border-emerald-500/40 bg-slate-900/80 p-4 space-y-3">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <span className="text-xs font-bold text-emerald-400 flex items-center gap-1.5">
                        <Check className="w-4 h-4 text-emerald-400" />
                        <span>Active Setup ({finalTrade.ticker || selectedTicker})</span>
                      </span>
                      <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 text-[10px] font-bold">
                        {finalTrade.market_bias || 'Approved'}
                      </span>
                    </div>

                    <div className="space-y-1.5 text-xs">
                      <div className="font-bold text-white text-sm">{finalTrade.strategy_name}</div>
                      <div className="grid grid-cols-2 gap-2 text-xs font-mono pt-1">
                        <div className="p-2 rounded bg-slate-950 border border-slate-800">
                          <span className="text-slate-400 block text-[10px]">Max Profit</span>
                          <span className="text-emerald-400 font-bold">
                            ₹{formatINR(finalTrade.max_profit || 0)}
                          </span>
                        </div>
                        <div className="p-2 rounded bg-slate-950 border border-slate-800">
                          <span className="text-slate-400 block text-[10px]">Max Loss</span>
                          <span className="text-rose-400 font-bold">
                            ₹{formatINR(finalTrade.max_loss || 0)}
                          </span>
                        </div>
                      </div>

                      <div className="p-2 rounded bg-slate-950 border border-slate-800 font-mono text-[11px] text-slate-300">
                        <div className="text-slate-400 text-[10px]">Net Premium Entry:</div>
                        <div className="font-bold text-white">₹{finalTrade.entry_price_net} per lot</div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-4 text-center text-xs text-slate-500">
                    <BarChart3 className="w-8 h-8 mx-auto mb-2 text-slate-600" />
                    <span>Click "START AGENT" to formulate and auto-trade an institutional options setup.</span>
                  </div>
                )}

                {/* Market Snapshot for Selected Symbol */}
                {marketData && (
                  <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 space-y-2 text-xs font-mono">
                    <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wide block">
                      Live Market Snapshot ({selectedTicker})
                    </span>
                    <div className="flex justify-between py-1 border-b border-slate-800 text-slate-300">
                      <span>Spot Price:</span>
                      <span className="text-white font-bold">₹{formatINR(marketData.spot_price)}</span>
                    </div>
                    <div className="flex justify-between py-1 border-b border-slate-800 text-slate-300">
                      <span>PCR Ratio:</span>
                      <span
                        className={`font-bold ${
                          (marketData.pcr || 1.0) >= 1.0 ? 'text-emerald-400' : 'text-rose-400'
                        }`}
                      >
                        {Number(marketData.pcr || 1.0).toFixed(2)}
                      </span>
                    </div>
                    <div className="flex justify-between py-1 text-slate-300">
                      <span>ATM IV:</span>
                      <span className="text-cyan-400 font-bold">{marketData.atm_iv || 15}%</span>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </main>
        )}

        {/* VIEW 2: PAPER PORTFOLIO & VIRTUAL LEDGER */}
        {activeTab === 'portfolio' && (
          <div className="flex-1 flex flex-col p-6 overflow-y-auto space-y-6">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <div>
                <h2 className="text-base font-bold text-white flex items-center gap-2">
                  <Briefcase className="w-5 h-5 text-cyan-400" />
                  <span>Paper Trading Virtual Ledger & Margin Engine</span>
                </h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  Institutional virtual ledger with real-time Black-Scholes MTM reconciliation.
                </p>
              </div>
              <button
                onClick={fetchPortfolio}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-white border border-slate-700 transition-colors"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isLoadingPortfolio ? 'animate-spin' : ''}`} />
                <span>Refresh Ledger</span>
              </button>
            </div>

            {/* OPEN POSITIONS TABLE */}
            <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-5 flex flex-col space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Activity className="w-4 h-4 text-cyan-400" />
                  <h3 className="text-sm font-bold text-white">
                    Active Open Positions ({(portfolio.open_positions && portfolio.open_positions.length) || 0})
                  </h3>
                </div>
              </div>

              <div className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-950">
                <table className="w-full text-left font-mono text-xs border-collapse">
                  <thead className="bg-slate-900/90 text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="py-2.5 px-3">Symbol</th>
                      <th className="py-2.5 px-3">Strategy</th>
                      <th className="py-2.5 px-3">Capital Locked</th>
                      <th className="py-2.5 px-3">Target</th>
                      <th className="py-2.5 px-3">Stop Loss</th>
                      <th className="py-2.5 px-3">Current MTM PnL</th>
                      <th className="py-2.5 px-3 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {!portfolio.open_positions || portfolio.open_positions.length === 0 ? (
                      <tr>
                        <td colSpan="7" className="text-center py-8 text-slate-600 font-sans text-xs">
                          No open positions currently active. Click "START AGENT" to analyze and execute an options setup.
                        </td>
                      </tr>
                    ) : (
                      portfolio.open_positions.map((pos) => (
                        <tr key={pos.id} className="hover:bg-slate-900/40 transition-colors">
                          <td className="py-2.5 px-3 font-bold text-white">{pos.ticker}</td>
                          <td className="py-2.5 px-3 text-slate-300 font-sans">{pos.strategy_name}</td>
                          <td className="py-2.5 px-3 text-cyan-300">
                            ₹{formatINR(pos.capital_allocated)}
                          </td>
                          <td className="py-2.5 px-3 text-emerald-400">₹{formatINR(pos.target)}</td>
                          <td className="py-2.5 px-3 text-rose-400">₹{formatINR(pos.stop_loss)}</td>
                          <td className="py-2.5 px-3 font-bold">
                            <span
                              className={`px-2 py-0.5 rounded text-xs ${
                                (pos.unrealized_pnl || 0) >= 0
                                  ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                                  : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
                              }`}
                            >
                              {(pos.unrealized_pnl || 0) >= 0 ? '+' : ''}₹{formatINR(pos.unrealized_pnl)}
                            </span>
                          </td>
                          <td className="py-2.5 px-3 text-right">
                            <button
                              onClick={() => handleClosePosition(pos.id)}
                              className="px-2.5 py-1 rounded bg-rose-600/80 hover:bg-rose-600 text-white font-sans text-[11px] font-semibold transition-all active:scale-95"
                            >
                              Close Now
                            </button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* CLOSED TRADES HISTORY TABLE */}
            <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-5 flex flex-col space-y-3">
              <div className="flex items-center gap-2">
                <Clock className="w-4 h-4 text-purple-400" />
                <h3 className="text-sm font-bold text-white">Closed Trades Audit History ({history.length})</h3>
              </div>

              <div className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-950">
                <table className="w-full text-left font-mono text-xs border-collapse">
                  <thead className="bg-slate-900/90 text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="py-2.5 px-3">Exit Date</th>
                      <th className="py-2.5 px-3">Symbol</th>
                      <th className="py-2.5 px-3">Strategy</th>
                      <th className="py-2.5 px-3">Exit Reason</th>
                      <th className="py-2.5 px-3">Duration</th>
                      <th className="py-2.5 px-3">Final P&L</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {history.length === 0 ? (
                      <tr>
                        <td colSpan="6" className="text-center py-8 text-slate-600 font-sans text-xs">
                          No closed trades recorded yet.
                        </td>
                      </tr>
                    ) : (
                      history.map((h) => (
                        <tr key={h.id} className="hover:bg-slate-900/40 transition-colors">
                          <td className="py-2.5 px-3 text-slate-400 text-[11px]">
                            {h.exit_timestamp ? new Date(h.exit_timestamp).toLocaleDateString() : '—'}
                          </td>
                          <td className="py-2.5 px-3 font-bold text-white">{h.ticker}</td>
                          <td className="py-2.5 px-3 text-slate-300 font-sans">{h.strategy_name}</td>
                          <td className="py-2.5 px-3">
                            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-300 border border-slate-700">
                              {h.status}
                            </span>
                          </td>
                          <td className="py-2.5 px-3 text-slate-400">{h.duration_minutes || 0} min</td>
                          <td className="py-2.5 px-3 font-bold">
                            <span
                              className={`px-2 py-0.5 rounded text-xs ${
                                (h.pnl || 0) >= 0
                                  ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                                  : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
                              }`}
                            >
                              {(h.pnl || 0) >= 0 ? '+' : ''}₹{formatINR(h.pnl)}
                            </span>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* VIEW 3: AGENT RETROSPECTIVE MEMORY */}
        {activeTab === 'memory' && (
          <div className="flex-1 flex flex-col p-6 overflow-y-auto space-y-6">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <div>
                <h2 className="text-base font-bold text-white flex items-center gap-2">
                  <Brain className="w-5 h-5 text-purple-400" />
                  <span>Agent Retrospective Memory (ChromaDB Reflections)</span>
                </h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  Vector-indexed post-mortems and rules learned from historical trades on {selectedTicker}.
                </p>
              </div>
              <button
                onClick={() => fetchLessons(selectedTicker)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-white border border-slate-700 transition-colors"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isLoadingLessons ? 'animate-spin' : ''}`} />
                <span>Re-query ChromaDB</span>
              </button>
            </div>

            <div className="p-5 rounded-xl border border-slate-800 bg-slate-900/60 backdrop-blur-md">
              <div className="prose prose-invert max-w-none text-xs font-mono leading-relaxed whitespace-pre-wrap text-slate-300">
                {lessonsMarkdown || 'Loading historical lessons from ChromaDB...'}
              </div>
            </div>

            <div className="p-4 rounded-xl border border-purple-500/30 bg-purple-950/20 text-xs text-purple-200">
              <strong className="block font-bold text-purple-400 uppercase text-[10px] mb-1">
                How Reflection Memory Works:
              </strong>
              Whenever a paper trade is closed (target, stop-loss, or manual exit), the Reflection Agent diagnoses
              the root cause and synthesizes a strict "Rule to Remember". This rule is stored in ChromaDB and
              automatically injected into the Quant Analyst prompt on subsequent runs for {selectedTicker}.
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function App() {
  return (
    <ErrorBoundary>
      <MainApp />
    </ErrorBoundary>
  )
}
