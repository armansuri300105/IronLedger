# STEP 13: 100% Autonomous Trading Loop & Auto-Execution Engine
CONTEXT: Eliminating manual approvals. The system must autonomously scan the market, trigger analysis, auto-execute orders when approved, and manage exits without human intervention.

INSTRUCTION:
Execute the following steps autonomously without asking for confirmation:

1. Update `backend/graph.py` (Direct Auto-Execution):
   - Add a new node: `order_execution_node(state: StrategistState)`:
     - Check `state["risk_approved"]`. If `True`:
       - Call `backend.execution.execute_paper_order(state["final_trade"])`.
       - Record execution status (`order_id`, fill price, timestamp) into `state["execution_details"]`.
       - Append a log: `"[AUTO-EXECUTION] Order placed automatically on virtual ledger: {strategy_name} at {fill_price}"` to `state["messages"]`.
     - If `False`:
       - Append log: `"[AUTO-SKIPPED] Risk Manager rejected proposal. No order placed."` to `state["messages"]`.
     - Return state.
   - Rewire the graph edges:
     - Conditional edge from `risk_manager`:
       - If rejected and `revision_count < 3`: route to `quant_analyst`.
       - If approved OR max revisions reached: route to `order_execution_node`.
     - Edge from `order_execution_node`: route directly to `END`.

2. Create `backend/autopilot.py` (The Autonomous Scheduler):
   - Global config:
     - `WATCHLIST = ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "INFY"]`
     - `MAX_OPEN_POSITIONS = 3`
     - `COOLDOWN_MINUTES_PER_TICKER = 30`
     - `IS_AUTOPILOT_ACTIVE = True` (in-memory flag with thread lock)
   - Function `is_market_open() -> bool`:
     - Checks if current IST time (UTC + 5:30) is between Monday to Friday, 09:15 AM and 03:15 PM.
     - Provide a dev bypass: `FORCE_MARKET_OPEN=true` in `.env` to allow offline testing on historical/replay data.
   - Function `run_autonomous_scanner()`:
     - Infinite async loop running every 60 seconds:
       - If not `IS_AUTOPILOT_ACTIVE` or not `is_market_open()`: sleep and continue.
       - Count currently `OPEN` positions in `paper_trader.db`. If `>= MAX_OPEN_POSITIONS`: log `"Max positions reached, skipping scan cycle."` and sleep.
       - Iterate through `WATCHLIST`:
         - Check if the ticker had an execution or scan within its cooldown window.
         - If eligible, invoke the compiled LangGraph pipeline for that ticker with initial state.
         - Stream and broadcast the autonomous decision logs over WebSocket/SSE.
         - Update last-scanned timestamp for that ticker.

3. Update `backend/position_monitor.py` (Autonomous Intraday Auto-Square-off):
   - Add an intraday safety rule: If current IST time reaches 15:15 PM, auto-close all open intraday positions at current market price with status `"CLOSED_EOD_SQUAREOFF"` to avoid overnight gap risk.

4. Add endpoints to `backend/main.py`:
   - `POST /api/autopilot/toggle`: Toggles `IS_AUTOPILOT_ACTIVE` (True/False) to serve as a kill-switch.
   - `GET /api/autopilot/status`: Returns whether autopilot is active, current watchlist, active scans, and cooldowns.
   - Register `run_autonomous_scanner()` in the FastAPI lifespan alongside `evaluate_open_positions()`.