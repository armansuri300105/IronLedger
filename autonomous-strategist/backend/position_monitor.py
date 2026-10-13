"""
Live Position Monitor & Exit Execution Engine
File: backend/position_monitor.py

Implements STEP 10:
- Continuous background monitoring of OPEN positions every 15 seconds
- Real-time MTM evaluation using live strike pricing from market_data.py
- Automatic Stop-Loss, Target, and Expiry triggers
- Ledger reconciliation (margin release, balance update, realized P&L commit)
- Automated trigger for post-mortem reflection agent (trigger_trade_postmortem)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from database import Account, Position, engine, get_account
from market_data import fetch_live_options_data
from reflection_agent import trigger_trade_postmortem
from autopilot import record_exit_log

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("position_monitor")

_monitor_running = False
_monitor_task: Optional[asyncio.Task] = None


def is_eod_squareoff_time() -> bool:
    """
    Intraday safety rule (STEP 13):
    Checks if current IST time reaches 15:15 PM (03:15 PM) or later on a weekday,
    or if SIMULATE_EOD_SQUAREOFF=true in .env to prevent overnight gap risk.
    """
    simulate = os.getenv("SIMULATE_EOD_SQUAREOFF", "").strip().lower() in ("true", "1", "yes")
    if simulate:
        return True

    now_ist = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))
    if now_ist.weekday() <= 4:
        if (now_ist.hour == 15 and now_ist.minute >= 15) or now_ist.hour > 15:
            return True
    return False



# ---------------------------------------------------------------------------
# 1. Position Evaluation & Exit Execution
# ---------------------------------------------------------------------------
def calculate_position_mtm(position: Position) -> Dict[str, Any]:
    """
    Computes real-time MTM and unrealized P&L for a given open position
    by comparing fill prices with live strike prices from market_data.
    """
    legs: List[Dict[str, Any]] = json.loads(position.legs_json or "[]")
    if not legs:
        return {"current_mtm": 0.0, "unrealized_pnl": 0.0, "exit_price": 0.0}

    market_info = fetch_live_options_data(position.ticker)
    live_strikes_map = {s["strike"]: s for s in market_info.get("strikes", [])}

    current_legs_value = 0.0
    entry_legs_value = 0.0
    lot_size = legs[0].get("lot_size", 25)
    qty = legs[0].get("qty", 1)

    for leg in legs:
        action = leg.get("action", "BUY").upper()
        strike = int(leg.get("strike", 0))
        op_type = leg.get("option_type", "CE").upper()
        fill_price = float(leg.get("fill_price", leg.get("base_ltp", 50.0)))

        # Find live strike price
        strike_data = live_strikes_map.get(strike, {})
        if op_type == "CE":
            current_ltp = float(strike_data.get("ce_ltp", fill_price))
        else:
            current_ltp = float(strike_data.get("pe_ltp", fill_price))

        if action == "BUY":
            entry_legs_value += fill_price * lot_size * qty
            current_legs_value += current_ltp * lot_size * qty
        else:
            entry_legs_value -= fill_price * lot_size * qty
            current_legs_value -= current_ltp * lot_size * qty

    # P&L calculation
    unrealized_pnl = round(current_legs_value - entry_legs_value, 2)
    net_unit_price = round(abs(current_legs_value) / (lot_size * qty), 2)

    return {
        "current_mtm": round(current_legs_value, 2),
        "unrealized_pnl": unrealized_pnl,
        "exit_price": net_unit_price,
        "spot_price": market_info.get("spot_price"),
    }


def close_position_record(
    session: Session,
    position: Position,
    status_reason: str,
    final_pnl: float,
    exit_price: float,
) -> Position:
    """Closes an open position, releases margin, reconciles cash balance, and triggers reflection."""
    account = get_account(session)

    position.status = status_reason
    position.exit_timestamp = datetime.now(timezone.utc)
    position.pnl = round(final_pnl, 2)
    position.exit_price = round(exit_price, 2)

    # Reconcile virtual ledger:
    # 1. Release locked margin
    account.margin_used = max(0.0, account.margin_used - position.capital_allocated)
    # 2. Add capital allocated + realized profit/loss back into cash balance
    account.virtual_balance += (position.capital_allocated + final_pnl)
    # 3. Update cumulative realized P&L
    account.realized_pnl += final_pnl

    session.add(position)
    session.add(account)
    session.commit()
    session.refresh(position)
    session.refresh(account)

    logger.info(
        f"Position #{position.id} ({position.ticker} {position.strategy_name}) CLOSED. "
        f"Reason: {status_reason} | PnL: INR {final_pnl:,.2f} | Balance: INR {account.virtual_balance:,.2f}"
    )

    # Trigger post-mortem learning agent in ChromaDB
    trigger_trade_postmortem(position)

    return position


def close_position_by_id(position_id: int, reason: str = "CLOSED_MANUAL") -> Dict[str, Any]:
    """Manually closes an active open position by ID."""
    with Session(engine) as session:
        position = session.get(Position, position_id)
        if not position or position.status != "OPEN":
            return {"success": False, "error": f"Position #{position_id} not found or not open."}

        mtm_data = calculate_position_mtm(position)
        pnl = mtm_data["unrealized_pnl"]
        exit_price = mtm_data["exit_price"]

        closed = close_position_record(session, position, reason, pnl, exit_price)

        pnl_sign = f"+INR {pnl:,.2f}" if pnl >= 0 else f"-INR {abs(pnl):,.2f}"
        record_exit_log({
            "type": "AUTO_EXIT",
            "badge_type": "MANUAL_CLOSE",
            "ticker": closed.ticker,
            "position_id": closed.id,
            "reason": f"Manual Close at INR {exit_price:,.2f} ({pnl_sign})",
            "pnl": pnl,
            "exit_price": exit_price,
            "message": f"Position #{closed.id} closed manually at INR {exit_price:,.2f} ({pnl_sign})",
        })

        return {
            "success": True,
            "position_id": closed.id,
            "ticker": closed.ticker,
            "status": closed.status,
            "pnl": closed.pnl,
            "exit_price": closed.exit_price,
            "message": f"Position #{closed.id} closed manually with PnL of INR {closed.pnl:,.2f}.",
        }


# ---------------------------------------------------------------------------
# 2. Background Monitor Loop
# ---------------------------------------------------------------------------
async def evaluate_open_positions():
    """Scans all OPEN positions and verifies target/stop-loss/intraday EOD exit criteria."""
    with Session(engine) as session:
        statement = select(Position).where(Position.status == "OPEN")
        open_positions = session.exec(statement).all()

        if not open_positions:
            return

        logger.info(f"Position Monitor: Evaluating {len(open_positions)} active open position(s)...")
        eod_triggered = is_eod_squareoff_time()

        for pos in open_positions:
            try:
                mtm_data = calculate_position_mtm(pos)
                unrealized_pnl = mtm_data["unrealized_pnl"]
                exit_price = mtm_data["exit_price"]
                spot_price = mtm_data.get("spot_price", 0.0)

                target = float(pos.target)
                stop_loss = float(pos.stop_loss)

                # Intraday Safety Check (STEP 13): 15:15 IST Auto-Squareoff
                if eod_triggered:
                    logger.info(
                        f"[EOD SQUAREOFF] 15:15 IST reached! Auto-closing #{pos.id} ({pos.ticker}) "
                        f"to prevent overnight gap risk."
                    )
                    close_position_record(session, pos, "CLOSED_EOD_SQUAREOFF", unrealized_pnl, exit_price)
                    pnl_sign = f"+INR {unrealized_pnl:,.2f}" if unrealized_pnl >= 0 else f"-INR {abs(unrealized_pnl):,.2f}"
                    record_exit_log({
                        "type": "AUTO_EXIT",
                        "badge_type": "EOD_SQUAREOFF",
                        "ticker": pos.ticker,
                        "position_id": pos.id,
                        "reason": f"15:15 IST Auto-Squareoff ({pnl_sign})",
                        "pnl": unrealized_pnl,
                        "exit_price": exit_price,
                        "spot_price": spot_price,
                        "message": f"15:15 IST Auto-Squareoff hit for #{pos.id} ({pos.ticker}) at INR {exit_price:,.2f} ({pnl_sign})",
                    })
                    continue

                # Exit Condition 1: Target Profit Achieved
                if target > 0 and unrealized_pnl >= target:
                    logger.info(f"Target Hit for #{pos.id} ({pos.ticker})! PnL: INR {unrealized_pnl:,.2f} >= Target INR {target:,.2f}")
                    close_position_record(session, pos, "CLOSED_TARGET", unrealized_pnl, exit_price)
                    record_exit_log({
                        "type": "AUTO_EXIT",
                        "badge_type": "TARGET_HIT",
                        "ticker": pos.ticker,
                        "position_id": pos.id,
                        "reason": f"Target Hit at INR {exit_price:,.2f} (+INR {unrealized_pnl:,.2f})",
                        "pnl": unrealized_pnl,
                        "exit_price": exit_price,
                        "spot_price": spot_price,
                        "message": f"Target Hit at INR {exit_price:,.2f} (+INR {unrealized_pnl:,.2f}) for #{pos.id} ({pos.ticker})",
                    })

                # Exit Condition 2: Stop-Loss Breached
                elif stop_loss > 0 and unrealized_pnl <= -abs(stop_loss):
                    logger.info(f"Stop-Loss Hit for #{pos.id} ({pos.ticker})! PnL: INR {unrealized_pnl:,.2f} <= SL INR {-abs(stop_loss):,.2f}")
                    close_position_record(session, pos, "CLOSED_SL", unrealized_pnl, exit_price)
                    record_exit_log({
                        "type": "AUTO_EXIT",
                        "badge_type": "SL_HIT",
                        "ticker": pos.ticker,
                        "position_id": pos.id,
                        "reason": f"SL Hit at INR {exit_price:,.2f} (-INR {abs(unrealized_pnl):,.2f})",
                        "pnl": unrealized_pnl,
                        "exit_price": exit_price,
                        "spot_price": spot_price,
                        "message": f"SL Hit at INR {exit_price:,.2f} (-INR {abs(unrealized_pnl):,.2f}) for #{pos.id} ({pos.ticker})",
                    })

            except Exception as err:
                logger.error(f"Error evaluating position #{pos.id}: {err}", exc_info=True)


async def _position_monitor_worker():
    """Asynchronous worker that runs evaluate_open_positions every 15 seconds."""
    global _monitor_running
    logger.info("Position Monitor background worker started (Interval: 15s).")
    while _monitor_running:
        try:
            await evaluate_open_positions()
        except Exception as exc:
            logger.error(f"Error in position monitor worker loop: {exc}")
        await asyncio.sleep(15)


def start_position_monitor():
    """Starts the background position monitor."""
    global _monitor_running, _monitor_task
    if not _monitor_running:
        _monitor_running = True
        loop = asyncio.get_event_loop()
        _monitor_task = loop.create_task(_position_monitor_worker())
        logger.info("Position monitor task initiated.")


def stop_position_monitor():
    """Cancels and halts the background position monitor."""
    global _monitor_running, _monitor_task
    _monitor_running = False
    if _monitor_task:
        _monitor_task.cancel()
        _monitor_task = None
        logger.info("Position monitor task stopped.")
