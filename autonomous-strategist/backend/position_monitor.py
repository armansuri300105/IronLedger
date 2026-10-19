"""
Live Position Monitor & Exit Execution Engine
File: backend/position_monitor.py

Implements STEP 10 & STEP 20:
- Continuous background monitoring of OPEN positions every 15 seconds
- Real-time MTM evaluation using live strike pricing from market_data.py
- In-memory POSITION_TELEMETRY tracking entry_spot and peak_pnl per position
- Rule A — Early Trend Breakdown Exit:
    * Bullish: spot < ema_20 and move_3m_pct <= -0.15% (or spot drops >= 0.25% below entry)
    * Bearish: spot > ema_20 and move_3m_pct >= +0.15% (or spot rises >= 0.25% above entry)
    * Closes position immediately with status CLOSED_TREND_REVERSAL
- Rule B — Trailing Profit Lock:
    * Once unrealized PnL >= 40% of target, locks trailing stop at 50% of peak_pnl
- Standard Target Hit, Stop-Loss Hit, and 15:15 IST EOD Auto-Squareoff
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
from execution import calculate_indian_fo_charges
from market_data import fetch_live_options_data
from reflection_agent import trigger_trade_postmortem
from autopilot import record_exit_log

# Use Uvicorn logger for unified console visibility
logger = logging.getLogger("uvicorn.error")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

_monitor_running = False
_monitor_task: Optional[asyncio.Task] = None

# In-memory tracking for active positions: {pos_id: {"entry_spot": float, "peak_pnl": float, "trailing_active": bool}}
POSITION_TELEMETRY: Dict[int, Dict[str, Any]] = {}

# Global in-memory tracking of last trade exit timestamp per ticker (UPGRADE 4 & 5)
LAST_EXIT_TIMESTAMP: Dict[str, datetime] = {}


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
def calculate_position_mtm(position: Position) -> Optional[Dict[str, Any]]:
    """
    Computes real-time MTM, unrealized P&L, live spot, EMA 20, completed 1m close, and trend state
    by valuing open positions with real order-book bid/ask quotes from market_data["all_strikes_map"].
    - Open BUY legs are valued at bidprice (to close = SELL at bid)
    - Open SELL legs are valued at askPrice (to close = BUY at ask)
    """
    legs: List[Dict[str, Any]] = json.loads(position.legs_json or "[]")
    if not legs:
        return {
            "current_mtm": 0.0,
            "unrealized_pnl": 0.0,
            "exit_price": 0.0,
            "spot_price": 0.0,
            "ema_20": 0.0,
            "move_3m_pct": 0.0,
            "last_closed_1m_close": 0.0,
            "trend_state": "CHOPPY_SIDEWAYS",
        }

    market_info = fetch_live_options_data(position.ticker)
    if not market_info:
        logger.warning(f"[POSITION MONITOR] Market data unavailable/timed out for {position.ticker}. Skipping MTM calculation.")
        return None

    all_strikes_map = market_info.get("all_strikes_map", {})
    live_strikes_map = {s["strike"]: s for s in market_info.get("strikes", [])}
    technicals = market_info.get("intraday_technicals", {})

    spot_price = float(market_info.get("spot_price", 0.0))
    ema_20 = float(technicals.get("ema_20", 0.0))
    move_3m_pct = float(technicals.get("move_3m_pct", 0.0))
    last_closed_1m_close = float(technicals.get("last_closed_1m_close", spot_price))
    trend_state = str(technicals.get("trend_state", "CHOPPY_SIDEWAYS"))

    current_legs_value = 0.0
    entry_legs_value = 0.0
    lot_size = legs[0].get("lot_size", 25)
    qty = legs[0].get("qty", 1)

    for leg in legs:
        action = leg.get("action", "BUY").upper()
        strike = int(leg.get("strike", 0))
        op_type = leg.get("option_type", "CE").upper()
        fill_price = float(leg.get("fill_price", leg.get("base_ltp", 50.0)))

        strike_data = all_strikes_map.get(strike) or live_strikes_map.get(strike, {})

        if op_type == "CE":
            ltp = float(strike_data.get("ce_ltp", fill_price))
            bid = float(strike_data.get("ce_bid", 0.0) or strike_data.get("bidprice", 0.0) or (ltp * 0.998))
            ask = float(strike_data.get("ce_ask", 0.0) or strike_data.get("askPrice", 0.0) or (ltp * 1.002))
        else:
            ltp = float(strike_data.get("pe_ltp", fill_price))
            bid = float(strike_data.get("pe_bid", 0.0) or strike_data.get("bidprice", 0.0) or (ltp * 0.998))
            ask = float(strike_data.get("pe_ask", 0.0) or strike_data.get("askPrice", 0.0) or (ltp * 1.002))

        # Open BUY leg is closed by selling at current market bid
        if action == "BUY":
            current_close_price = bid if bid > 0 else ltp
            entry_legs_value += fill_price * lot_size * qty
            current_legs_value += current_close_price * lot_size * qty
        # Open SELL leg is closed by buying back at current market ask
        else:
            current_close_price = ask if ask > 0 else ltp
            entry_legs_value -= fill_price * lot_size * qty
            current_legs_value -= current_close_price * lot_size * qty

    unrealized_pnl = round(current_legs_value - entry_legs_value, 2)
    net_unit_price = round(abs(current_legs_value) / (lot_size * qty), 2)

    # Estimate round-trip brokerage & taxes (UPGRADE 1)
    estimated_charges = calculate_indian_fo_charges(legs, lot_size=lot_size, lots=qty, is_round_trip=True)
    unrealized_net_pnl = round(unrealized_pnl - estimated_charges, 2)

    return {
        "current_mtm": round(current_legs_value, 2),
        "gross_pnl": unrealized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "net_pnl": unrealized_net_pnl,
        "estimated_charges": estimated_charges,
        "exit_price": net_unit_price,
        "spot_price": spot_price,
        "ema_20": ema_20,
        "move_3m_pct": move_3m_pct,
        "last_closed_1m_close": last_closed_1m_close,
        "trend_state": trend_state,
    }


def close_position_record(
    session: Session,
    position: Position,
    status_reason: str,
    final_pnl: float,
    exit_price: float,
    trend_state: Optional[str] = None,
) -> Position:
    """Closes an open position, releases margin, reconciles cash balance with Net PnL, and triggers reflection."""
    account = get_account(session)

    legs: List[Dict[str, Any]] = json.loads(position.legs_json or "[]")
    lot_size = legs[0].get("lot_size", 25) if legs else 25
    qty = legs[0].get("qty", 1) if legs else 1

    # UPGRADE 1: Deduct total Indian F&O charges from realized PnL
    charges = calculate_indian_fo_charges(legs, lot_size=lot_size, lots=qty, is_round_trip=True)
    gross_pnl = round(final_pnl, 2)
    net_pnl = round(gross_pnl - charges, 2)

    position.status = status_reason
    position.exit_timestamp = datetime.now(timezone.utc)
    position.gross_pnl = gross_pnl
    position.charges = charges
    position.pnl = net_pnl
    position.exit_price = round(exit_price, 2)

    # Reconcile virtual ledger with true Net PnL:
    account.margin_used = max(0.0, account.margin_used - position.capital_allocated)
    account.virtual_balance += (position.capital_allocated + net_pnl)
    account.realized_pnl += net_pnl

    session.add(position)
    session.add(account)
    session.commit()
    session.refresh(position)
    session.refresh(account)

    # UPGRADE 4: Preserve Intraday Candle Memory — NEVER clear MINUTE_CANDLES or LIVE_SPOT_HISTORY!
    # Instead, record LAST_EXIT_TIMESTAMP for the 15-minute anti-churn cooldown.
    clean_ticker = position.ticker.strip().upper()
    LAST_EXIT_TIMESTAMP[clean_ticker] = datetime.now(timezone.utc)
    logger.info(
        f"[INTRADAY MEMORY PRESERVED] Preserved MINUTE_CANDLES & LIVE_SPOT_HISTORY for {clean_ticker}. "
        f"Registered 15-minute post-exit cooldown timestamp ({LAST_EXIT_TIMESTAMP[clean_ticker].strftime('%H:%M:%S')} UTC)."
    )

    # Explicit trade closed logging with Gross PnL, Charges, and Net PnL
    logger.info(
        f"[TRADE CLOSED] {position.ticker} {position.strategy_name} | Reason: {status_reason} | "
        f"Gross PnL: ₹{gross_pnl:,.2f} | Charges: ₹{charges:,.2f} | Net PnL: ₹{net_pnl:,.2f}"
    )

    logger.info(
        f"Position #{position.id} ({position.ticker} {position.strategy_name}) CLOSED. "
        f"Reason: {status_reason} | Gross PnL: INR {gross_pnl:,.2f} | Charges: INR {charges:,.2f} | "
        f"Net PnL: INR {net_pnl:,.2f} | Balance: INR {account.virtual_balance:,.2f}"
    )

    if not trend_state and position.id is not None:
        trend_state = POSITION_TELEMETRY.get(position.id, {}).get("trend_state")

    # Clean up in-memory telemetry
    if position.id is not None:
        POSITION_TELEMETRY.pop(position.id, None)

    # Trigger post-mortem learning agent in ChromaDB (STEP 22: passes trend_state)
    trigger_trade_postmortem(position, trend_state=trend_state)

    return position


def close_position_by_id(position_id: int, reason: str = "CLOSED_MANUAL") -> Dict[str, Any]:
    """Manually closes an active open position by ID."""
    with Session(engine) as session:
        position = session.get(Position, position_id)
        if not position or position.status != "OPEN":
            return {"success": False, "error": f"Position #{position_id} not found or not open."}

        mtm_data = calculate_position_mtm(position)
        if not mtm_data:
            pnl = 0.0
            exit_price = float(position.entry_price or 0.0)
        else:
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
# 2. Background Monitor Loop with Smart Reversal & Trailing Stop Exits
# ---------------------------------------------------------------------------
async def evaluate_open_positions():
    """Scans all OPEN positions and verifies target/stop-loss/intraday EOD/smart reversal exits."""
    with Session(engine) as session:
        statement = select(Position).where(Position.status == "OPEN")
        open_positions = session.exec(statement).all()

        if not open_positions:
            return

        logger.info(f"Position Monitor: Evaluating {len(open_positions)} active open position(s)...")
        eod_triggered = is_eod_squareoff_time()

        for pos in open_positions:
            try:
                pos_id = pos.id
                if pos_id is None:
                    continue

                mtm_data = calculate_position_mtm(pos)
                if not mtm_data:
                    logger.info(
                        f"[POSITION MONITOR] Skipping evaluation for position #{pos_id} ({pos.ticker}) this tick due to market data timeout."
                    )
                    continue

                unrealized_pnl = mtm_data["unrealized_pnl"]
                exit_price = mtm_data["exit_price"]
                spot_price = mtm_data.get("spot_price", 0.0)
                ema_20 = mtm_data.get("ema_20", 0.0)
                move_3m_pct = mtm_data.get("move_3m_pct", 0.0)
                last_closed_1m_close = float(mtm_data.get("last_closed_1m_close", spot_price))
                trend_state = str(mtm_data.get("trend_state", "CHOPPY_SIDEWAYS"))

                target = float(pos.target)
                stop_loss = float(pos.stop_loss)

                # STEP 24: 45-Second Grace Period after entry_timestamp
                now_utc = datetime.now(timezone.utc)
                entry_ts = pos.entry_timestamp
                if entry_ts:
                    if entry_ts.tzinfo is None:
                        entry_ts = entry_ts.replace(tzinfo=timezone.utc)
                    elapsed_seconds = (now_utc - entry_ts).total_seconds()
                else:
                    elapsed_seconds = 999.0

                is_in_grace_period = elapsed_seconds < 45.0
                effective_sl = stop_loss * 1.5 if is_in_grace_period else stop_loss

                # Initialize in-memory position telemetry (entry_spot, peak_pnl, trend_state)
                if pos_id not in POSITION_TELEMETRY:
                    POSITION_TELEMETRY[pos_id] = {
                        "entry_spot": spot_price,
                        "peak_pnl": unrealized_pnl,
                        "trailing_active": False,
                        "trend_state": trend_state,
                    }
                else:
                    POSITION_TELEMETRY[pos_id]["trend_state"] = trend_state
                    if unrealized_pnl > POSITION_TELEMETRY[pos_id]["peak_pnl"]:
                        POSITION_TELEMETRY[pos_id]["peak_pnl"] = unrealized_pnl

                telemetry = POSITION_TELEMETRY[pos_id]
                entry_spot = telemetry["entry_spot"]
                peak_pnl = telemetry["peak_pnl"]

                # Determine trade directional bias (Bullish vs Bearish)
                strat_lower = (pos.strategy_name or "").lower()
                legs = json.loads(pos.legs_json or "[]")
                is_bullish = any(k in strat_lower for k in ["bull", "call spread", "long ce", "ce buy", "bull call"])
                is_bearish = any(k in strat_lower for k in ["bear", "put spread", "long pe", "pe buy", "bear put"])

                if not is_bullish and not is_bearish and legs:
                    net_delta = sum(
                        (1 if l.get("action", "BUY").upper() == "BUY" else -1)
                        * (float(l.get("delta", 0.5)) if l.get("option_type", "CE").upper() == "CE" else -abs(float(l.get("delta", 0.5))))
                        for l in legs
                    )
                    is_bullish = net_delta >= 0
                    is_bearish = net_delta < 0

                # 1. 15:15 IST Auto-Squareoff (EOD)
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

                # 2. Target Profit Achieved
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
                    continue

                # 3. Stop-Loss Breached (with STEP 24 45-Second Grace Period)
                if stop_loss > 0 and unrealized_pnl <= -abs(effective_sl):
                    logger.info(
                        f"Stop-Loss Hit for #{pos.id} ({pos.ticker})! "
                        f"PnL: INR {unrealized_pnl:,.2f} <= SL INR {-abs(effective_sl):,.2f} "
                        f"(Grace Period: {is_in_grace_period}, Age: {elapsed_seconds:.1f}s)"
                    )
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
                    continue

                # -------------------------------------------------------------------
                # STEP 22 Rule A — 1-Minute Candle Close Trend Breakdown Auto-Exits
                # -------------------------------------------------------------------
                pos_ticker = pos.ticker.upper()
                reversal_pts = 85.0 if pos_ticker == "BANKNIFTY" else 35.0

                # Bullish: completed 1m candle close below ema_20 AND live spot drops >= 35 pts (NIFTY) or >= 85 pts (BANKNIFTY) below entry_spot
                bullish_reversal = (
                    is_bullish
                    and not is_in_grace_period
                    and (last_closed_1m_close < ema_20)
                    and (entry_spot - spot_price >= reversal_pts)
                )

                # Bearish: completed 1m candle close above ema_20 AND live spot rises >= 35 pts (NIFTY) or >= 85 pts (BANKNIFTY) above entry_spot
                bearish_reversal = (
                    is_bearish
                    and not is_in_grace_period
                    and (last_closed_1m_close > ema_20)
                    and (spot_price - entry_spot >= reversal_pts)
                )

                # Fallback for percentage breakdown on equities or if entry_spot is uncalibrated
                if not (bullish_reversal or bearish_reversal) and pos_ticker not in ("NIFTY", "BANKNIFTY", "FINNIFTY") and not is_in_grace_period:
                    spot_offset_pct = (((spot_price - entry_spot) / entry_spot * 100.0) if entry_spot > 0 else 0.0)
                    if is_bullish and (last_closed_1m_close < ema_20) and (spot_offset_pct <= -0.40):
                        bullish_reversal = True
                    elif is_bearish and (last_closed_1m_close > ema_20) and (spot_offset_pct >= 0.40):
                        bearish_reversal = True

                if bullish_reversal:
                    logger.info(
                        f"[EARLY TREND REVERSAL] #{pos.id} ({pos.ticker}) Bullish structure broken! "
                        f"1m Close: {last_closed_1m_close} < EMA20: {ema_20}, Drop from entry: {entry_spot - spot_price:.2f} pts (threshold: {reversal_pts} pts). "
                        f"Closing immediately to prevent rollover loss."
                    )
                    close_position_record(session, pos, "CLOSED_TREND_REVERSAL", unrealized_pnl, exit_price, trend_state=trend_state)
                    pnl_sign = f"+INR {unrealized_pnl:,.2f}" if unrealized_pnl >= 0 else f"-INR {abs(unrealized_pnl):,.2f}"
                    record_exit_log({
                        "type": "AUTO_EXIT",
                        "badge_type": "TREND_REVERSAL",
                        "ticker": pos.ticker,
                        "position_id": pos.id,
                        "reason": f"Trend Reversal Breakdown ({pnl_sign})",
                        "pnl": unrealized_pnl,
                        "exit_price": exit_price,
                        "spot_price": spot_price,
                        "message": f"Early Trend Breakdown exit for #{pos.id} ({pos.ticker}) at INR {exit_price:,.2f} ({pnl_sign})",
                    })
                    continue

                if bearish_reversal:
                    logger.info(
                        f"[EARLY TREND REVERSAL] #{pos.id} ({pos.ticker}) Bearish structure broken! "
                        f"1m Close: {last_closed_1m_close} > EMA20: {ema_20}, Rise from entry: {spot_price - entry_spot:.2f} pts (threshold: {reversal_pts} pts). "
                        f"Closing immediately to prevent rollover loss."
                    )
                    close_position_record(session, pos, "CLOSED_TREND_REVERSAL", unrealized_pnl, exit_price, trend_state=trend_state)
                    pnl_sign = f"+INR {unrealized_pnl:,.2f}" if unrealized_pnl >= 0 else f"-INR {abs(unrealized_pnl):,.2f}"
                    record_exit_log({
                        "type": "AUTO_EXIT",
                        "badge_type": "TREND_REVERSAL",
                        "ticker": pos.ticker,
                        "position_id": pos.id,
                        "reason": f"Trend Reversal Breakdown ({pnl_sign})",
                        "pnl": unrealized_pnl,
                        "exit_price": exit_price,
                        "spot_price": spot_price,
                        "message": f"Early Trend Breakdown exit for #{pos.id} ({pos.ticker}) at INR {exit_price:,.2f} ({pnl_sign})",
                    })
                    continue

                # -------------------------------------------------------------------
                # UPGRADE 4: Trailing Stop "Brokerage Dead Zone" Fix
                # -------------------------------------------------------------------
                capital_locked = float(pos.capital_allocated or 0.0)
                estimated_charges = float(mtm_data.get("estimated_charges", 120.0))
                min_arm_threshold = estimated_charges + max(300.0, 0.035 * capital_locked)
                target_arm_level = 0.65 * target

                if target > 0:
                    # Never arm Trailing Stop until Gross PnL >= (estimated_charges + max(300.0, 0.035 * capital_locked))
                    # and Gross PnL reaches 65% of gross_target_rupees
                    if peak_pnl >= target_arm_level and peak_pnl >= min_arm_threshold:
                        if not telemetry.get("trailing_active"):
                            telemetry["trailing_active"] = True
                            logger.info(
                                f"[TRAILING STOP ARMED] #{pos.id} ({pos.ticker}) Peak Gross PnL INR {peak_pnl:,.2f} "
                                f">= 65% of target (INR {target_arm_level:,.2f}) and cleared charges threshold "
                                f"INR {min_arm_threshold:,.2f}. Locking trailing floor at 55% of peak Gross PnL."
                            )

                    if telemetry.get("trailing_active"):
                        trailing_level = 0.55 * peak_pnl
                        if unrealized_pnl <= trailing_level:
                            logger.info(
                                f"[TRAILING STOP HIT] #{pos.id} ({pos.ticker}) Gross PnL INR {unrealized_pnl:,.2f} "
                                f"fell to trailing floor INR {trailing_level:,.2f} (55% of peak INR {peak_pnl:,.2f}). Locking net profit."
                            )
                            close_position_record(session, pos, "CLOSED_TRAILING_STOP", unrealized_pnl, exit_price)
                            pnl_sign = f"+INR {unrealized_pnl:,.2f}" if unrealized_pnl >= 0 else f"-INR {abs(unrealized_pnl):,.2f}"
                            record_exit_log({
                                "type": "AUTO_EXIT",
                                "badge_type": "TRAILING_STOP",
                                "ticker": pos.ticker,
                                "position_id": pos.id,
                                "reason": f"Trailing Profit Lock Hit at INR {exit_price:,.2f} ({pnl_sign})",
                                "pnl": unrealized_pnl,
                                "exit_price": exit_price,
                                "spot_price": spot_price,
                                "message": f"Trailing Stop Hit for #{pos.id} ({pos.ticker}) at INR {exit_price:,.2f} ({pnl_sign})",
                            })
                            continue

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
        try:
            loop = asyncio.get_running_loop()
            _monitor_task = loop.create_task(_position_monitor_worker())
        except RuntimeError:
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
