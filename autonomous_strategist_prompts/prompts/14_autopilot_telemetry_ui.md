# STEP 14: Autonomous Mission Control & Kill-Switch UI
CONTEXT: Converting the frontend into an autonomous telemetry cockpit where the user monitors the agent's hands-free activity, live decisions, and kill-switch state.

INSTRUCTION:
Execute the following steps autonomously without asking for confirmation:

1. Update `frontend/src/App.jsx`:
   - Add an **Autopilot Status Bar** at the very top:
     - **Status Badge:** Pulsing green `"AUTOPILOT: ACTIVE (SCANNING)"` or red `"AUTOPILOT: PAUSED"`.
     - **Emergency Kill Switch:** A prominent toggle button hitting `POST /api/autopilot/toggle`.
     - **Market Session Indicator:** Displays IST time and market session status (`Open` / `Closed` / `Bypass Active`).
     - **Risk Cap Indicator:** Shows `Active Positions: X / 3`.

2. Replace manual form submission with **Live Autonomous Activity Stream**:
   - Instead of a manual "Analyze Ticker" button, render a live ticker selector showing which symbol the agent is currently scanning or last traded.
   - Connect via WebSocket / SSE to a unified `/api/stream/live` feed that broadcasts whatever the agent is doing across the entire watchlist in real time.
   - Highlight auto-executions with distinct styled badge cards:
     - Green border for `AUTO_BUY` / `AUTO_SELL`.
     - Yellow border for `RISK_REJECTED` (with the Risk Manager's critique).
     - Blue border for `POSTMORTEM_SAVED` (showing the new rule written to ChromaDB).

3. Add **Autonomous Health & P&L Telemetry Cards**:
   - Total Capital: ₹10,00,000 base virtual fund.
   - Net Realized P&L + Unrealized MTM updated live every 5 seconds.
   - Auto-Exit Log: Displays exact triggers hit (e.g., `"Target Hit at ₹24,850 (+₹4,200)"`, `"SL Hit at ₹24,400 (-₹2,100)"`, `"15:15 IST Auto-Squareoff"`).