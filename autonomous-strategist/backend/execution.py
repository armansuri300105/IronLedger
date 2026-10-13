"""
Paper Trading Execution Engine & Realistic Margin Simulator
File: backend/execution.py

Implements STEP 9:
- Realistic fills with +0.2% (Buy) / -0.2% (Sell) slippage
- Index lot sizing (NIFTY: 25, BANKNIFTY: 15, FINNIFTY: 25)
- Strict virtual margin verification and ledger allocation
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlmodel import Session

from database import Account, Position, engine, get_account

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("paper_execution_engine")

# Standard NSE derivative lot sizes
LOT_SIZES: Dict[str, int] = {
    "NIFTY": 25,
    "BANKNIFTY": 15,
    "FINNIFTY": 25,
    "MIDCPNIFTY": 50,
    "RELIANCE": 250,
    "TCS": 175,
    "INFY": 400,
    "HDFCBANK": 550,
    "ICICIBANK": 700,
}

SLIPPAGE_PCT = 0.002  # 0.2% slippage on fills


def get_lot_size(ticker: str) -> int:
    """Returns the market lot size for the given ticker."""
    clean = ticker.strip().upper()
    return LOT_SIZES.get(clean, 1)


def execute_paper_order(trade_proposal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes a virtual options/equity order against the paper trading database:
    - Calculates realistic execution fills with bid/ask slippage
    - Verifies available virtual capital
    - Locks margin and commits an OPEN position
    """
    ticker = trade_proposal.get("ticker", "NIFTY").strip().upper()
    strategy_name = trade_proposal.get("strategy_name", "Custom Options Strategy")
    raw_legs = trade_proposal.get("legs", [])
    max_loss = float(trade_proposal.get("max_loss", 5000.0))
    stop_loss = float(trade_proposal.get("stop_loss", max_loss * 0.7))
    target = float(trade_proposal.get("target", float(trade_proposal.get("max_profit", 3500.0))))
    thesis = str(trade_proposal.get("rationale", trade_proposal.get("thesis", "Algorithmic entry")))

    lot_size = get_lot_size(ticker)
    qty = int(trade_proposal.get("lot_size", 1))

    logger.info(f"Executing paper order for [{ticker}] - Strategy: {strategy_name} (Lot Size: {lot_size})...")

    # Step A: Compute filled legs with realistic slippage
    filled_legs: List[Dict[str, Any]] = []
    total_debit = 0.0
    total_credit = 0.0

    for leg in raw_legs:
        action = leg.get("action", "BUY").upper()
        strike = int(leg.get("strike", 0))
        option_type = leg.get("option_type", leg.get("type", "CE")).upper()
        expiry = leg.get("expiry", "Current")
        base_ltp = float(leg.get("ltp", leg.get("premium", 50.0)))
        delta = float(leg.get("delta", 0.5))

        # Slippage: BUY fills slightly higher (+0.2%), SELL fills slightly lower (-0.2%)
        if action == "BUY":
            fill_price = round(base_ltp * (1.0 + SLIPPAGE_PCT), 2)
            total_debit += fill_price * lot_size * qty
        else:
            fill_price = round(base_ltp * (1.0 - SLIPPAGE_PCT), 2)
            total_credit += fill_price * lot_size * qty

        filled_legs.append({
            "action": action,
            "strike": strike,
            "option_type": option_type,
            "expiry": expiry,
            "base_ltp": base_ltp,
            "fill_price": fill_price,
            "lot_size": lot_size,
            "qty": qty,
            "delta": delta,
        })

    net_premium = round((total_credit - total_debit) / (lot_size * qty), 2)

    # Required capital: For defined-risk credit spreads, it's max_loss; for debits, it's total_debit
    if total_debit > total_credit:
        required_capital = round(total_debit, 2)
    else:
        required_capital = round(max(max_loss, 2500.0), 2)

    # Step B: Ledger verification and margin allocation
    with Session(engine) as session:
        account = get_account(session)
        available_cash = account.virtual_balance - account.margin_used

        if available_cash < required_capital:
            logger.warning(
                f"Paper trade rejected: INSUFFICIENT_MARGIN. Required: INR {required_capital:,.2f}, Available: INR {available_cash:,.2f}"
            )
            return {
                "success": False,
                "error": "INSUFFICIENT_MARGIN",
                "message": f"Requires INR {required_capital:,.2f}, but only INR {available_cash:,.2f} available in virtual cash.",
                "required_capital": required_capital,
                "available_cash": available_cash,
            }

        # Deduct capital from cash and lock in margin_used
        account.virtual_balance -= required_capital
        account.margin_used += required_capital

        # Create Position record
        new_position = Position(
            ticker=ticker,
            strategy_name=strategy_name,
            legs_json=json.dumps(filled_legs),
            net_premium=net_premium,
            capital_allocated=required_capital,
            stop_loss=stop_loss,
            target=target,
            entry_timestamp=datetime.now(timezone.utc),
            status="OPEN",
            thesis=thesis,
        )

        session.add(account)
        session.add(new_position)
        session.commit()
        session.refresh(new_position)
        session.refresh(account)

        logger.info(
            f"Paper trade filled! Position ID #{new_position.id} committed. "
            f"Capital Locked: INR {required_capital:,.2f} | Remaining Free Cash: INR {account.virtual_balance:,.2f}"
        )

        return {
            "success": True,
            "position_id": new_position.id,
            "ticker": ticker,
            "strategy_name": strategy_name,
            "status": "OPEN",
            "capital_allocated": required_capital,
            "net_premium": net_premium,
            "filled_legs": filled_legs,
            "virtual_balance": account.virtual_balance,
            "margin_used": account.margin_used,
            "entry_timestamp": new_position.entry_timestamp.isoformat(),
            "message": f"Order filled on NSE Paper Engine. Capital locked: INR {required_capital:,.2f}.",
        }


if __name__ == "__main__":
    from database import init_db
    init_db()
    test_trade = {
        "ticker": "NIFTY",
        "strategy_name": "NIFTY Bull Call Spread",
        "legs": [
            {"action": "BUY", "strike": 22350, "type": "CE", "ltp": 165.0, "delta": 0.54},
            {"action": "SELL", "strike": 22400, "type": "CE", "ltp": 98.0, "delta": 0.38},
        ],
        "max_profit": 1675.0,
        "max_loss": 825.0,
        "stop_loss": 500.0,
        "target": 1200.0,
        "rationale": "Bullish PCR test run",
    }
    res = execute_paper_order(test_trade)
    print("Execution Result:", json.dumps(res, indent=2))
