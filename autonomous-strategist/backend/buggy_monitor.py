"""
Trading Buggy - Shadow Monitor & Signal Emitter
File: backend/buggy_monitor.py

Implements STEP 15 & STEP 20:
- Read-only parallel monitor watching active paper trades in paper_trader.db
- Emits real-time actionable signals (BUY_SIGNAL, REVERSAL_ALERT, SELL_SIGNAL, ACTIVE_POSITION_SYNC) via SSE
- Deduplication: Module-level EMITTED_BUY_IDS prevents re-emitting BUY alert beeps on client reconnect
- Emits BUY_SIGNAL only for trades entered within the last 90 seconds
- Zero interference with LangGraph multi-agent pipeline or execution logic
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from sqlmodel import Session, select

from database import Position, engine
from execution import get_lot_size, LOT_SIZES
from market_data import fetch_live_options_data

# Use Uvicorn logger for unified console visibility
logger = logging.getLogger("uvicorn.error")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# Persistent module-level cache and deduplication sets across all SSE connections
EMITTED_BUY_IDS: Set[int] = set()
_entry_spot_cache: Dict[int, float] = {}
_last_reversal_alert_time: Dict[int, float] = {}


def infer_strategy_bias(strategy_name: str, legs_json: str = "[]") -> str:
    """
    Infers directional market bias (BULLISH, BEARISH, or NEUTRAL)
    based on the strategy title and option legs structure.
    """
    strat = (strategy_name or "").lower()
    if any(k in strat for k in ["bull", "call spread", "long ce", "ce buy", "bull call"]):
        return "BULLISH"
    if any(k in strat for k in ["bear", "put spread", "long pe", "pe buy", "bear put"]):
        return "BEARISH"

    try:
        legs = json.loads(legs_json or "[]")
        net_delta = 0.0
        for leg in legs:
            action = leg.get("action", "BUY").upper()
            op_type = leg.get("option_type", "CE").upper()
            delta = float(leg.get("delta", 0.5))
            sign = 1.0 if action == "BUY" else -1.0
            if op_type == "PE":
                delta = -abs(delta)
            net_delta += sign * delta

        if net_delta > 0.15:
            return "BULLISH"
        elif net_delta < -0.15:
            return "BEARISH"
    except Exception:
        pass

    return "NEUTRAL"


def format_exit_reason(status_code: str) -> str:
    """Converts database status enum into clean trader-facing exit instruction."""
    code = (status_code or "").upper()
    if "TREND_REVERSAL" in code:
        return "Early Trend Reversal Auto-Exit"
    if "TRAILING" in code:
        return "Trailing Profit Lock Hit"
    if "TARGET" in code:
        return "Target Hit"
    if "SL" in code or "STOP" in code:
        return "Stop-Loss Hit"
    if "EOD" in code or "SQUAREOFF" in code:
        return "15:15 IST Market Close Auto-Squareoff"
    if "EXPIRY" in code:
        return "Option Expiry Reached"
    if "MANUAL" in code:
        return "Manual Close"
    return "Order Exited"


async def get_live_spot_price(ticker: str) -> float:
    """Safely retrieves live spot price asynchronously without blocking the loop."""
    try:
        data = await asyncio.to_thread(fetch_live_options_data, ticker)
        if not data:
            return 0.0
        return float(data.get("spot_price", 0.0))
    except Exception as exc:
        logger.warning(f"Failed fetching live spot for {ticker} in Buggy: {exc}")
        return 0.0


async def stream_buggy_signals() -> AsyncGenerator[str, None]:
    """
    Asynchronously monitors open positions and emits Server-Sent Events (SSE)
    every 5 seconds:
    - BUY_SIGNAL: Truly new open position created within the last 90 seconds
    - ACTIVE_POSITION_SYNC: Existing open positions on reconnection without alert beeps
    - REVERSAL_ALERT: Spot price moves sharply (>0.5%) against trade bias
    - SELL_SIGNAL: Positions transitioned from OPEN to CLOSED_*
    """
    seen_client_open_ids: Set[int] = set()
    seen_closed_ids: Set[int] = set()

    logger.info("Trading Buggy SSE client connected. Starting shadow monitoring loop...")

    # Yield initial connection handshake
    handshake = {
        "type": "BUGGY_CONNECTED",
        "message": "Trading Buggy Shadow Monitor connected to live market feeds.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    yield f"data: {json.dumps(handshake)}\n\n"

    while True:
        try:
            with Session(engine) as session:
                # -----------------------------------------------------------------
                # 1. Evaluate OPEN Positions
                # -----------------------------------------------------------------
                statement = select(Position).where(Position.status == "OPEN")
                open_positions = session.exec(statement).all()
                current_open_ids = {p.id for p in open_positions if p.id is not None}

                for pos in open_positions:
                    if pos.id is None:
                        continue

                    legs = json.loads(pos.legs_json or "[]")
                    bias = infer_strategy_bias(pos.strategy_name, pos.legs_json)

                    # Determine and cache entry spot price
                    if pos.id not in _entry_spot_cache:
                        live_spot = await get_live_spot_price(pos.ticker)
                        if live_spot <= 0 and legs:
                            live_spot = float(legs[0].get("strike", 0.0))
                        _entry_spot_cache[pos.id] = live_spot

                    entry_spot = _entry_spot_cache.get(pos.id, 0.0)

                    # A. Position Signal Emission (Deduplicated across reconnects)
                    if pos.id not in seen_client_open_ids:
                        seen_client_open_ids.add(pos.id)

                        strikes_list = [f"{l.get('strike')} {l.get('option_type')}" for l in legs]
                        ticker_clean = (pos.ticker or "NIFTY").strip().upper()
                        fallback_lot = get_lot_size(ticker_clean)
                        lot_size = legs[0].get("lot_size", fallback_lot) if legs else fallback_lot
                        qty = legs[0].get("qty", 1) if legs else 1
                        trade_type = "DEBIT" if pos.net_premium < 0 else "CREDIT"

                        base_payload = {
                            "position_id": pos.id,
                            "ticker": pos.ticker,
                            "strategy_name": pos.strategy_name,
                            "bias": bias,
                            "legs": legs,
                            "strikes": strikes_list,
                            "recommended_entry_premium": abs(round(pos.net_premium, 2)),
                            "net_premium": round(pos.net_premium, 2),
                            "trade_type": trade_type,
                            "lot_size": lot_size,
                            "qty": qty,
                            "total_quantity": lot_size * qty,
                            "target": round(pos.target, 2),
                            "stop_loss": round(pos.stop_loss, 2),
                            "capital_allocated": round(pos.capital_allocated, 2),
                            "entry_spot_price": round(entry_spot, 2),
                            "entry_timestamp": pos.entry_timestamp.isoformat() if pos.entry_timestamp else "",
                            "thesis": pos.thesis,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }

                        # Check age of position in seconds
                        now_utc = datetime.now(timezone.utc)
                        entry_ts = pos.entry_timestamp
                        if entry_ts:
                            if entry_ts.tzinfo is None:
                                entry_ts = entry_ts.replace(tzinfo=timezone.utc)
                            age_sec = (now_utc - entry_ts).total_seconds()
                        else:
                            age_sec = 999999.0

                        # STEP 20: Only emit BUY_SIGNAL if opened within last 90 seconds AND not previously emitted
                        if pos.id not in EMITTED_BUY_IDS and age_sec <= 90.0:
                            EMITTED_BUY_IDS.add(pos.id)
                            buy_payload = {
                                **base_payload,
                                "type": "BUY_SIGNAL",
                                "message": f"ACTIONABLE SIGNAL: {pos.ticker} {pos.strategy_name} ({bias})",
                            }
                            logger.info(f"[BUGGY] Emitting BUY_SIGNAL for #{pos.id} ({pos.ticker})")
                            yield f"data: {json.dumps(buy_payload)}\n\n"
                        else:
                            # Existing older open positions sync without re-triggering new Buy beep
                            sync_payload = {
                                **base_payload,
                                "type": "ACTIVE_POSITION_SYNC",
                                "message": f"ACTIVE POSITION SYNC: {pos.ticker} {pos.strategy_name} ({bias})",
                            }
                            logger.info(f"[BUGGY] Emitting ACTIVE_POSITION_SYNC for #{pos.id} ({pos.ticker})")
                            yield f"data: {json.dumps(sync_payload)}\n\n"

                    # B. REVERSAL ALERT EVALUATION
                    current_spot = await get_live_spot_price(pos.ticker)
                    if current_spot > 0 and entry_spot > 0:
                        pct_change = (current_spot - entry_spot) / entry_spot
                        is_reversal = False
                        detail_msg = ""

                        if bias == "BULLISH" and pct_change <= -0.005:  # Dropped >= 0.5%
                            is_reversal = True
                            detail_msg = (
                                f"{pos.ticker} spot dropped {abs(pct_change)*100:.2f}% below entry "
                                f"(Entry: ₹{entry_spot:,.2f} -> Live: ₹{current_spot:,.2f})."
                            )
                        elif bias == "BEARISH" and pct_change >= 0.005:  # Rose >= 0.5%
                            is_reversal = True
                            detail_msg = (
                                f"{pos.ticker} spot rose {abs(pct_change)*100:.2f}% above entry "
                                f"(Entry: ₹{entry_spot:,.2f} -> Live: ₹{current_spot:,.2f})."
                            )

                        if is_reversal:
                            now_ts = asyncio.get_event_loop().time()
                            last_alert = _last_reversal_alert_time.get(pos.id, 0.0)

                            if (now_ts - last_alert) >= 15.0:
                                _last_reversal_alert_time[pos.id] = now_ts
                                reversal_payload = {
                                    "type": "REVERSAL_ALERT",
                                    "position_id": pos.id,
                                    "ticker": pos.ticker,
                                    "strategy_name": pos.strategy_name,
                                    "bias": bias,
                                    "entry_spot_price": round(entry_spot, 2),
                                    "current_spot_price": round(current_spot, 2),
                                    "spot_change_pct": round(pct_change * 100, 2),
                                    "detail": detail_msg,
                                    "warning": "⚠️ REVERSAL DETECTED: Consider Manual Demat Exit Now.",
                                    "message": f"⚠️ REVERSAL ALERT for #{pos.id} ({pos.ticker}): {detail_msg} Market reversing. Consider squaring off in your Demat account (Angel One/Groww/Zerodha) before Stop-Loss is hit.",
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
                                logger.warning(f"[BUGGY] Emitting REVERSAL_ALERT for #{pos.id} ({pos.ticker})")
                                yield f"data: {json.dumps(reversal_payload)}\n\n"

                # -----------------------------------------------------------------
                # 2. Evaluate Recently CLOSED Positions (SELL_SIGNAL)
                # -----------------------------------------------------------------
                cutoff = datetime.now(timezone.utc) - timedelta(seconds=12)
                closed_stmt = (
                    select(Position)
                    .where(Position.status != "OPEN")
                    .order_by(Position.exit_timestamp.desc())
                )
                closed_positions = session.exec(closed_stmt).all()

                for pos in closed_positions:
                    if pos.id is None or pos.id in seen_closed_ids:
                        continue

                    recently_closed = False
                    if pos.id in seen_client_open_ids:
                        recently_closed = True
                    elif pos.exit_timestamp and pos.exit_timestamp >= cutoff:
                        recently_closed = True

                    if recently_closed:
                        seen_closed_ids.add(pos.id)
                        seen_client_open_ids.discard(pos.id)

                        reason_label = format_exit_reason(pos.status)
                        sell_payload = {
                            "type": "SELL_SIGNAL",
                            "position_id": pos.id,
                            "ticker": pos.ticker,
                            "strategy_name": pos.strategy_name,
                            "legs": json.loads(pos.legs_json or "[]"),
                            "capital_allocated": round(pos.capital_allocated or 0.0, 2),
                            "status": pos.status,
                            "exit_reason": reason_label,
                            "exit_price": round(pos.exit_price or 0.0, 2),
                            "pnl": round(pos.pnl or 0.0, 2),
                            "exit_timestamp": pos.exit_timestamp.isoformat() if pos.exit_timestamp else datetime.now(timezone.utc).isoformat(),
                            "instruction": f"TRADE SIGNAL EXIT: {reason_label}. Square off your position in your Demat account.",
                            "message": f"TRADE SIGNAL COMPLETED #{pos.id} ({pos.ticker}): {reason_label} | Exit Price: ₹{pos.exit_price or 0:,.2f} | PnL: ₹{pos.pnl or 0:,.2f}",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        logger.info(f"[BUGGY] Emitting SELL_SIGNAL for #{pos.id} ({pos.ticker})")
                        yield f"data: {json.dumps(sell_payload)}\n\n"

            # -----------------------------------------------------------------
            # 3. Heartbeat Keep-Alive (every 5-second interval)
            # -----------------------------------------------------------------
            heartbeat = {
                "type": "HEARTBEAT",
                "open_count": len(current_open_ids),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            yield f"data: {json.dumps(heartbeat)}\n\n"

        except asyncio.CancelledError:
            logger.info("Trading Buggy stream client disconnected.")
            break
        except Exception as exc:
            logger.error(f"Error in Buggy monitor loop: {exc}", exc_info=True)

        await asyncio.sleep(5)
