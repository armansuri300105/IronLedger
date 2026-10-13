# Autonomous Equity & Options Strategist

An autonomous multi-agent derivatives trading system orchestrating market data ingestion, fundamental research, quantitative strategy formulation, strict risk governance, paper trading ledger execution, and continuous reflection memory via LangGraph, FastAPI, ChromaDB, and React.

## Directory Structure

```
autonomous-strategist/
├── backend/
│   ├── database.py         # SQLite models (Account, Position) via SQLModel
│   ├── execution.py        # Paper order fills, lot sizing, and margin verification
│   ├── position_monitor.py # Background MTM evaluator & SL/Target exit execution
│   ├── reflection_agent.py # Post-mortem learning agent storing rules in ChromaDB
│   ├── market_data.py      # Live NSE options scraper & Black-Scholes Greeks
│   ├── rag_engine.py       # ChromaDB vector store, live news RSS & reflections
│   ├── graph.py            # LangGraph multi-agent cycle with memory loop
│   ├── main.py             # FastAPI server streaming SSE & portfolio REST APIs
│   ├── requirements.txt    # Python dependencies
│   └── paper_trader.db     # SQLite paper trading ledger database
├── frontend/
│   ├── src/
│   │   ├── App.jsx         # 3-view React dashboard: Strategist, Portfolio & Memory
│   │   ├── main.jsx        # React root
│   │   └── index.css       # TailwindCSS styling
│   ├── package.json
│   └── vite.config.js
└── venv/                   # Python virtual environment
```

## Features

1. **Autonomous LangGraph Multi-Agent Cycle:**
   - **Data Engineer:** Live NSE options chain data, Put-Call Ratio (PCR), and Black-Scholes Greeks (Delta, Gamma, Theta, Vega).
   - **RAG Researcher:** Real-time Google News RSS and ChromaDB vector retrieval for market catalysts.
   - **Quant Analyst:** Derivative strategy formulation reading live strikes and Greeks with memory of past mistakes.
   - **Risk Manager:** Capital and delta checks enforcing strict desk risk limits with a bounded feedback loop.

2. **Paper Trading Ledger & Execution Engine:**
   - Realistic slippage (+0.2% on Buy, -0.2% on Sell).
   - Margin requirement checks against a starting virtual cash pool of ₹10,00,000.
   - Instant execution fills via `POST /api/trade/execute`.

3. **Background Position Monitor:**
   - Asynchronous background worker polling open positions every 15 seconds.
   - Live MTM evaluation and automated execution of Stop-Loss, Target, and Expiry exits.
   - Manual exit trigger via `POST /api/positions/{id}/close`.

4. **Self-Improving Memory & Post-Mortem Loop:**
   - On trade closure, the **Reflection Agent** evaluates the outcome (WIN/LOSS).
   - Synthesizes a strict "Rule to Remember" persisted into ChromaDB.
   - Injects relevant historical rules into future Quant Analyst prompts to avoid repeating past mistakes.

5. **React Dashboard:**
   - **Strategist View:** Top Metrics Bar, Live NSE Greeks table, Agent reasoning terminal, and "Execute Paper Trade" button.
   - **Portfolio View:** Real-time cash balance, margin used, realized P&L, win-rate %, active positions table with "Close Now" action, and trade history table.
   - **Memory View:** Visual inspector for rules and lessons learned from past trades.

## Running the Platform

### Terminal 1 — Start the Backend:
```powershell
cd "autonomous-strategist"
.\venv\Scripts\activate
python backend/main.py
```
- Server: `http://localhost:8000`
- Interactive API Docs: `http://localhost:8000/docs`

### Terminal 2 — Start the Frontend:
```powershell
cd "autonomous-strategist\frontend"
npm run dev
```
- Dashboard: `http://localhost:5173`
