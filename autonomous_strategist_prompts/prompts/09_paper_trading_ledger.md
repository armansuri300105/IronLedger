# STEP 9: Virtual Ledger, Margin Rules & Execution Engine
CONTEXT: Building an execution engine that simulates realistic options and equity order fills with virtual capital, slippage, and margin checks.

INSTRUCTION:
Execute the following steps autonomously without asking for confirmation:

1. In the `backend` directory, install:
   `pip install sqlmodel aiosqlite tabulate` and update `backend/requirements.txt`.

2. Create `backend/database.py` and define SQLite models using SQLModel:
   - `Account`: `id` (int), `virtual_balance` (float, default 1000000.0), `margin_used` (float, default 0.0), `realized_pnl` (float, default 0.0), `created_at` (datetime).
   - `Position`:
     - `id` (int), `ticker` (str), `strategy_name` (str)
     - `legs_json` (str, stores list of legs: action, strike, option_type, qty, lot_size, entry_price)
     - `net_premium` (float)
     - `capital_allocated` (float)
     - `stop_loss` (float), `target` (float)
     - `entry_timestamp` (datetime), `exit_timestamp` (datetime, nullable)
     - `exit_price` (float, nullable), `pnl` (float, nullable)
     - `status` (str: "OPEN", "CLOSED_TARGET", "CLOSED_SL", "CLOSED_EXPIRY")
     - `thesis` (str, Quant's original hypothesis)
   - Initialize database `backend/paper_trader.db` on startup and ensure a default account with ₹10,00,000 virtual balance exists.

3. Create `backend/execution.py`:
   - Lot size dictionary for Indian indices & stocks (e.g., `{"NIFTY": 25, "BANKNIFTY": 15, "FINNIFTY": 25}`, default equity `1`).
   - `execute_paper_order(trade_proposal: dict) -> dict`:
     - Calculate required margin / premium outlay for all legs:
       - For BUY legs: `premium = strike_ltp * qty * lot_size`.
       - Apply realistic slippage: Add +0.2% to Ask for BUY, subtract -0.2% from Bid for SELL.
     - Check if `account.virtual_balance - account.margin_used >= required_capital`. If insufficient, reject with `"INSUFFICIENT_MARGIN"`.
     - Deduct capital from `account.virtual_balance`, increase `account.margin_used`, and commit the new `Position` with status `"OPEN"`.
     - Return execution confirmation including exact fill prices and position ID.

4. Add endpoints to `backend/main.py`:
   - `GET /api/portfolio`: Returns current cash balance, margin used, realized P&L, and open positions.
   - `POST /api/trade/execute`: Triggers `execute_paper_order` from the latest approved LangGraph trade proposal.