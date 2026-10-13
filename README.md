# IronLedger — Autonomous Equity & Options Strategist

IronLedger is a production-grade, 100% hands-free autonomous derivatives trading and risk management platform. Built on LangGraph, FastAPI, Google Gemini 2.5 Flash, ChromaDB, SQLModel, and React (Vite + Tailwind CSS), it provides automated options scanning, real-time Black-Scholes Greeks calculation, quantitative strategy formulation, Chief Risk Officer governance, paper trading ledger execution, and continuous post-mortem reflection memory.

---

## Key System Architecture

```
                                  [ START ]
                                      │
                                      ▼
                      ┌───────────────────────────────┐
                      │      1. Data Engineer         │
                      │  Live NSE Chains & BS Greeks  │
                      └───────────────┬───────────────┘
                                      │
                                      ▼
                      ┌───────────────────────────────┐
                      │      2. RAG Researcher        │
                      │ Google News RSS & ChromaDB RAG│
                      └───────────────┬───────────────┘
                                      │
                                      ▼
                      ┌───────────────────────────────┐
             ┌───────►│      3. Quant Analyst         │◄────────┐
             │        │    Gemini 2.5 Flash Model     │         │
             │        └───────────────┬───────────────┘         │
             │                        │                         │
             │                        ▼                         │
             │        ┌───────────────────────────────┐         │
             │        │       4. Risk Manager         │         │
             │        │ Desk Hard Bounds & Delta Check│         │
             │        └───────────────┬───────────────┘         │
             │                        │                         │
      [Rejected &                     ├─────────────────────────┘
     Revisions < 3]                   │
                                      ▼ [Approved OR Revisions >= 3]
                      ┌───────────────────────────────┐
                      │    5. Auto-Execution Node     │
                      │  Committed to Virtual Ledger  │
                      └───────────────┬───────────────┘
                                      │
                                      ▼
                                   [ END ]
```

---

## Core Capabilities

1. **Autonomous LangGraph Multi-Agent Engine**:
   - **Data Engineer**: Synthesizes real-time NSE spot prices, Put-Call Ratio (PCR), and exact Black-Scholes Greeks (Delta, Gamma, Theta, Vega) across live strikes.
   - **RAG Researcher**: Ingests fresh financial news catalysts from Google News RSS and searches vector-indexed historical intelligence in ChromaDB.
   - **Quant Analyst**: Leverages Google Gemini 2.5 Flash (`langchain-google-genai`) to formulate defined-risk multi-leg derivative spreads (Bull Call Spread, Bear Put Spread, Iron Condor).
   - **Chief Risk Officer**: Enforces non-negotiable hard desk constraints (Max Loss cap <= ₹15,000, reward-to-risk ratio >= 0.6, strike validity) with an automated feedback loop.
   - **Auto-Execution Node**: Automatically fills approved trades directly into the virtual paper ledger without requiring human intervention.

2. **Autonomous Autopilot Scheduler & Telemetry**:
   - **Continuous Watchlist Radar**: Cycles through major indices and liquid F&O stocks (`NIFTY`, `BANKNIFTY`, `RELIANCE`, `HDFCBANK`, `INFY`).
   - **Risk Cap Guard**: Restricts concurrent active positions to `MAX_OPEN_POSITIONS = 3`.
   - **Cooldown Mechanism**: Enforces a 30-minute cooldown window per ticker to prevent over-trading.
   - **Market Hours Enforcement**: Checks Indian trading hours (09:15 AM - 03:15 PM IST, Monday-Friday) with a dev bypass (`FORCE_MARKET_OPEN=true`).

3. **Intraday Auto-Squareoff Safeguard**:
   - If current IST time reaches **15:15 PM**, all open intraday positions are automatically closed at prevailing market rates with status `CLOSED_EOD_SQUAREOFF` to eliminate overnight gap risk.

4. **Self-Improving ChromaDB Reflection Loop**:
   - When trades exit (Target Hit, Stop Loss, or EOD Square-off), the **Reflection Agent** analyzes the trade's P&L and records actionable "Rules to Remember" into ChromaDB.
   - Past mistakes are dynamically injected into subsequent Quant Analyst prompts to continuously adapt to market conditions.

5. **Mission Control & Autopilot Cockpit UI**:
   - **Instrument Launcher**: Select from Major Indices, F&O Equities, or enter custom NSE tickers.
   - **Start Agent Button**: One-click launch with real-time 5-stage visual pipeline progress tracker.
   - **Emergency Kill Switch**: Instant pause/resume toggle for continuous autopilot scanning.
   - **Live Health Cards**: Real-time virtual capital (₹10,00,000 base fund), Net Realized P&L + MTM, win-rate metrics, and Auto-Exit trigger log.

---

## Directory Structure

```
IronLedger/
├── autonomous-strategist/
│   ├── backend/
│   │   ├── autopilot.py          # 24/7 autonomous loop & telemetry broadcaster
│   │   ├── database.py           # SQLModel virtual ledger (Account, Position)
│   │   ├── execution.py          # Order fills, slippage (+0.2%/-0.2%), and margin locks
│   │   ├── graph.py              # Compiled LangGraph multi-agent architecture
│   │   ├── main.py               # FastAPI server with SSE streaming & REST APIs
│   │   ├── market_data.py        # Live NSE options chain & Black-Scholes Greeks
│   │   ├── position_monitor.py   # Background MTM evaluator & 15:15 IST auto-squareoff
│   │   ├── rag_engine.py         # Google News RSS and ChromaDB vector store
│   │   ├── reflection_agent.py   # Trade post-mortem learning agent
│   │   ├── requirements.txt      # Python backend dependencies
│   │   ├── paper_trader.db       # SQLite virtual ledger database
│   │   └── chroma_db/            # Persistent ChromaDB vector collections
│   └── frontend/
│       ├── src/
│       │   ├── App.jsx           # Mission Control, Portfolio, and Memory Dashboard
│       │   ├── main.jsx          # React entrypoint
│       │   └── index.css         # TailwindCSS v4 stylesheet
│       ├── package.json          # Node dependencies
│       └── vite.config.js        # Vite configuration
├── autonomous_strategist_prompts/ # Complete step-by-step build specifications
├── .env.example                  # Environment configuration template
├── .gitignore                    # Git ignore file
└── README.md                     # Project documentation
```

---

## Getting Started

### Prerequisites
- Python 3.10+
- Node.js 18+
- Google Gemini API Key (or Groq API Key)

### 1. Backend Setup

```bash
cd autonomous-strategist/backend

# Create and activate virtual environment
python -m venv ../venv
# Windows:
..\venv\Scripts\activate
# Linux/macOS:
# source ../venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env and insert your GOOGLE_API_KEY / GEMINI_API_KEY

# Launch the FastAPI backend
python main.py
# Server runs on http://127.0.0.1:8000
```

### 2. Frontend Setup

```bash
cd autonomous-strategist/frontend

# Install dependencies
npm install

# Start the Vite development server
npm run dev
# Dashboard runs on http://localhost:5173
```

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Service health status |
| `GET` | `/api/stream/live` | Unified SSE telemetry feed broadcasting all autopilot scans |
| `GET` | `/api/stream/{ticker}` | Real-time multi-agent LangGraph SSE stream for a specific ticker |
| `GET` | `/api/autopilot/status` | Current autopilot state, IST session, active scans, and cooldowns |
| `POST` | `/api/autopilot/toggle` | Emergency kill switch to pause or resume autopilot scanning |
| `POST` | `/api/autopilot/scan` | On-demand scan trigger for a specific symbol |
| `GET` | `/api/portfolio` | Virtual ledger balance, locked margin, realized P&L, and open positions |
| `POST` | `/api/trade/execute` | Fills an approved options proposal on the virtual ledger |
| `GET` | `/api/positions/history` | Audit log of all closed trades with final P&L and duration |
| `POST` | `/api/positions/{id}/close` | Manually exits an active position and triggers post-mortem reflection |
| `GET` | `/api/lessons/{ticker}` | Queries ChromaDB for learned post-mortem rules for an asset |

---

## License

MIT License. Designed for quantitative derivatives trading research, algorithmic simulation, and autonomous agent orchestration.
