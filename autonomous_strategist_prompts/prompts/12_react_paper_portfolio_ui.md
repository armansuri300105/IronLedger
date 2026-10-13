# STEP 12: React Paper Trading Dashboard & Learned Memory View
CONTEXT: Upgrading `frontend/src/App.jsx` with a Paper Trading management interface, live P&L tracker, and an Agent Memory inspector.

INSTRUCTION:
Execute the following steps autonomously without asking for confirmation:

1. In `frontend/src/App.jsx`, add tabs or split views for:
   - **Strategy Generator** (Existing LangGraph terminal & live options chain)
   - **Paper Portfolio** (Virtual funds, open positions, trade history)
   - **Agent Memory** (Retrieved post-mortems & learned trading rules)

2. Implement the **Paper Portfolio View**:
   - Header Bar:
     - Virtual Cash: ₹[value] (Green/Neutral)
     - Margin Used: ₹[value]
     - Realized P&L: ₹[value] (Green if > 0, Red if < 0)
     - Win Rate %: [wins / total trades]
   - Open Positions Table:
     - Columns: Symbol, Strategy, Legs, Entry Premium, Current MTM, Unrealized P&L, Stop Loss, Target, Action (Manual "Close Now" button hitting `POST /api/positions/{id}/close`).
   - Closed Trades History Table:
     - Columns: Date, Symbol, Strategy, Exit Reason, Final P&L (₹), Post-Mortem summary.

3. Implement the **Agent Execution Action**:
   - In the Strategy Generator panel, when `finalTrade` is produced by LangGraph:
     - Add an **"Execute Paper Trade"** button.
     - When clicked, sends `finalTrade` to `POST /api/trade/execute`.
     - Shows an instant toast notification: "Order Filled on NSE Paper Engine at ₹[price]".

4. Implement the **Agent Memory & Lessons Panel**:
   - Create a drawer or side card: "Agent Retrospective Memory (ChromaDB)".
   - Queries `GET /api/lessons/{ticker}` to display past rules the agent generated after previous losses (e.g., "Avoid selling OTM calls when NIFTY PCR > 1.2").