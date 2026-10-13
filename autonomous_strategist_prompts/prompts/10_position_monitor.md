# STEP 10: Live Position Monitor & Exit Execution
CONTEXT: Implementing a continuous background monitor in FastAPI that tracks live MTM (Mark-to-Market), evaluates Stop-Loss / Target triggers, and closes positions.

INSTRUCTION:
Execute the following steps autonomously without asking for confirmation:

1. Create `backend/position_monitor.py`:
   - Define `evaluate_open_positions()` as an async task running every 15 seconds:
     - Query all `Position` records where `status == "OPEN"`.
     - For each open position:
       - Fetch current market prices for the option strikes/equities from `backend/market_data.py`.
       - Compute current net MTM value of the combined legs.
       - Calculate current unrealized P&L in ₹: `current_mtm - capital_allocated`.
       - Check Exit Conditions:
         1. **Target Hit:** Current P&L >= `position.target`
         2. **Stop-Loss Hit:** Current P&L <= `-abs(position.stop_loss)`
         3. **Expiry Exit:** If current date >= expiry date and time >= 15:15 IST.
       - If any exit condition triggers:
         - Close the position: set `status` to `CLOSED_TARGET`, `CLOSED_SL`, or `CLOSED_EXPIRY`.
         - Set `exit_timestamp` to `datetime.utcnow()`.
         - Compute final `pnl`.
         - Update `Account`: Release `margin_used`, add `capital_allocated + pnl` back to `virtual_balance`, and update `realized_pnl`.
         - Call the reflection agent hook: `trigger_trade_postmortem(closed_position)`.

2. In `backend/main.py`:
   - Register the position monitor using FastAPI lifespan events (`@asynccontextmanager`) so it runs as an asynchronous background loop on server start and cancels cleanly on shutdown.
   - Add endpoint `GET /api/positions/history`: Returns closed trades with entry, exit, duration, and final P&L.