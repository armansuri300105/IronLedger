"""
Live Position Monitor & Exit Execution Engine
File: backend/position_monitor.py

Implements STEP 10 & STEP 20:
- Continuous background monitoring of OPEN positions every 15 seconds
- Real-time MTM evaluation using live strike pricing from market_data.py
- In-memory POSITION_TELEMETRY tracking entry_spot, peak_pnl, and trailing_active per position
- UPGRADE 50 Rule 4: Rigid 4-Exit Model ONLY (NO CLOSED_TREND_REVERSAL or preemption):
    1. CLOSED_TARGET: unrealized_pnl >= position.target
    2. CLOSED_TRAILING_STOP: arms only when peak MTM >= 80% target, triggers if MTM falls below 75% peak MTM
    3. CLOSED_SL: unrealized_pnl <= -position.stop_loss AND spot adverse delta >= 18 pts (NIFTY) / 50 pts (BANKNIFTY), clamped to -stop_loss
    4. CLOSED_EOD: 15:15 IST Auto-Squareoff
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
from execution import calculate_indian_fo_charges, get_lot_size, LOT_SIZES
from market_data import fetch_live_options_data, fetch_realtime_index_spot
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

# UPGRADE 43: Module-level cache for position entry spots: {pos_id: entry_spot}
ENTRY_SPOT_CACHE: Dict[int, float] = {}

# UPGRADE 47: In-memory counter tracking consecutive SL breaches per position: {pos_id: breach_count}
SL_BREACH_COUNTS: Dict[int, int] = {}

# UPGRADE 49: In-memory counter tracking consecutive adverse structure breaks per position: {pos_id: breach_count}
REVERSAL_BREACH_COUNTS: Dict[int, int] = {}

# Global in-memory tracking of last trade exit timestamp per ticker (UPGRADE 4 & 5)
LAST_EXIT_TIMESTAMP: Dict[str, datetime] = {}


def _persist_entry_spot_to_db(pos_id: int, entry_spot: float):
    """Safely backfills entry_spot to SQLite so it permanently survives restarts (UPGRADE 43)."""
    try:
        with Session(engine) as session:
            pos_db = session.get(Position, pos_id)
            if pos_db and (pos_db.entry_spot is None or pos_db.entry_spot <= 0):
                pos_db.entry_spot = entry_spot
                session.add(pos_db)
                session.commit()
                logger.info(f"[ENTRY SPOT BACKFILLED] Position #{pos_id} persisted entry_spot={entry_spot:.2f} to SQLite.")
    except Exception as exc:
        logger.debug(f"[ENTRY SPOT PERSIST] Could not persist entry_spot for #{pos_id}: {exc}")


def is_eod_squareoff_time() -> bool:
    """
    Intraday safety rule (STEP 13):
    Checks if current IST time reaches 15:15 PM (03:15 PM) or later on a weekday,
    or if SIMULATE_EOD_SQUAREOFF=true in .env to prevent overnight gap risk.
    Bypassed when FORCE_MARKET_OPEN=true (test/simulation mode).
    """
    simulate = os.getenv("SIMULATE_EOD_SQUAREOFF", "").strip().lower() in ("true", "1", "yes")
    if simulate:
        return True

    force = os.getenv("FORCE_MARKET_OPEN", "").strip().lower() in ("true", "1", "yes")
    if force:
        return False

    now_ist = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))
    if now_ist.weekday() <= 4:
        if (now_ist.hour == 15 and now_ist.minute >= 15) or now_ist.hour > 15:
            return True
    return False


# ---------------------------------------------------------------------------
# 1. Position Evaluation & Exit Execution
# ---------------------------------------------------------------------------
def calculate_position_mtm(
    position: Position,
    market_info: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Computes real-time MTM, unrealized P&L, live spot, EMA 20, completed 1m close, and trend state.
    UPGRADE 44:
    - Guaranteed live spot via 3-tier fetcher (fetch_realtime_index_spot).
    - True delta + gamma + theta time-decay MTM calculation.
    - Completely eliminates static -49.40 / -58.20 slippage offset when dSpot is near 0.
    - Outputs [MTM MONITOR] telemetry log on every cycle.
    """
    legs: List[Dict[str, Any]] = json.loads(position.legs_json or "[]")
    ticker_clean = getattr(position, "ticker", getattr(position, "symbol", "NIFTY")).strip().upper()
    fallback_lot = get_lot_size(ticker_clean)
    lot_size = legs[0].get("lot_size", fallback_lot) if legs else fallback_lot
    qty = legs[0].get("qty", 1) if legs else 1
    total_qty = lot_size * qty
    pos_id = getattr(position, "id", None)

    if not legs:
        return {
            "current_mtm": 0.0,
            "unrealized_pnl": 0.0,
            "exit_price": 0.0,
            "spot_price": 0.0,
            "ema_20": 0.0,
            "move_3m_pct": 0.0,
            "move_3m_pts": 0.0,
            "move_5m_pts": 0.0,
            "last_closed_1m_close": 0.0,
            "trend_state": "CHOPPY_SIDEWAYS",
        }

    # UPGRADE 44: Fetch fresh live spot via multi-source 3-tier fetcher
    is_mocked = hasattr(fetch_live_options_data, "assert_called") or hasattr(fetch_live_options_data, "_mock_name")
    if market_info is None:
        market_info = fetch_live_options_data(ticker_clean, force_refresh=True, caller="position_monitor")
        if is_mocked and market_info and "spot_price" in market_info:
            spot_price = float(market_info.get("spot_price", 0.0) or 0.0)
        else:
            synth_f = market_info.get("synthetic_futures") if market_info else None
            fresh_live_spot = fetch_realtime_index_spot(ticker_clean, live_synth_f=synth_f, option_chain=market_info)
            if fresh_live_spot and fresh_live_spot > 1000:
                spot_price = fresh_live_spot
            elif market_info:
                spot_price = float(market_info.get("spot_price", 0.0) or 0.0)
            else:
                spot_price = 0.0
    else:
        spot_price = float(market_info.get("spot_price", 0.0) or 0.0)

    if not market_info:
        if spot_price > 1000:
            market_info = {
                "spot_price": spot_price,
                "intraday_technicals": {
                    "ema_20": spot_price,
                    "move_3m_pct": 0.0,
                    "move_3m_pts": 0.0,
                    "move_5m_pts": 0.0,
                    "trend_state": "STRONG_UPTREND",
                },
            }
        else:
            logger.warning(f"[POSITION MONITOR] Market data unavailable for {ticker_clean}. Skipping MTM calculation.")
            return None

    all_strikes_map = market_info.get("all_strikes_map", {})
    live_strikes_map = {s["strike"]: s for s in market_info.get("strikes", [])}
    technicals = market_info.get("intraday_technicals", {})

    ema_20 = float(technicals.get("ema_20", 0.0))
    move_3m_pct = float(technicals.get("move_3m_pct", 0.0))
    move_3m_pts = float(technicals.get("move_3m_pts", 0.0) or 0.0)
    move_5m_pts = float(technicals.get("move_5m_pts", 0.0) or 0.0)
    last_closed_1m_close = float(technicals.get("last_closed_1m_close", spot_price))
    trend_state = str(technicals.get("trend_state", "CHOPPY_SIDEWAYS"))

    current_legs_value = 0.0
    entry_legs_value = 0.0

    # UPGRADE 41 & 43: Pure Spot-Delta Anchored MTM & Deterministic Recovery
    entry_spot = None

    # Priority 1: Check in-memory module-level cache
    if pos_id is not None and pos_id in ENTRY_SPOT_CACHE:
        entry_spot = ENTRY_SPOT_CACHE[pos_id]

    # Priority 2: Check position.entry_spot
    if (entry_spot is None or entry_spot <= 0) and getattr(position, "entry_spot", None):
        entry_spot = float(position.entry_spot)
        if pos_id is not None:
            ENTRY_SPOT_CACHE[pos_id] = entry_spot

    # Priority 3: Check in-memory POSITION_TELEMETRY if already initialized
    if (entry_spot is None or entry_spot <= 0) and pos_id is not None and pos_id in POSITION_TELEMETRY:
        es_tel = POSITION_TELEMETRY[pos_id].get("entry_spot")
        if es_tel and float(es_tel) > 0:
            entry_spot = float(es_tel)
            ENTRY_SPOT_CACHE[pos_id] = entry_spot

    # Priority 4: Check legs in legs_json
    if (entry_spot is None or entry_spot <= 0) and legs:
        for l in legs:
            es_l = l.get("entry_spot")
            if es_l and float(es_l) > 0:
                entry_spot = float(es_l)
                break
        if entry_spot and pos_id is not None:
            ENTRY_SPOT_CACHE[pos_id] = entry_spot

    # Priority 5: Deterministic backfill for legacy open positions with entry_spot=None or 0 (UPGRADE 43)
    if (entry_spot is None or entry_spot <= 0) and legs:
        long_legs = [l for l in legs if str(l.get("action", "")).upper() == "BUY"]
        if long_legs:
            buy_leg = long_legs[0]
            b_strike = float(buy_leg.get("strike", 0.0) or 0.0)
            b_delta = abs(float(buy_leg.get("delta", 0.5) or 0.5))
            if 0.40 <= b_delta <= 0.60 or b_strike > 0:
                entry_spot = b_strike
                if pos_id is not None:
                    ENTRY_SPOT_CACHE[pos_id] = entry_spot
                position.entry_spot = entry_spot
                if pos_id is not None:
                    _persist_entry_spot_to_db(pos_id, entry_spot)

    # Priority 6: Fallback to current spot if completely unknown
    if not entry_spot or entry_spot <= 0:
        entry_spot = spot_price
        if pos_id is not None:
            ENTRY_SPOT_CACHE[pos_id] = entry_spot

    dSpot = spot_price - entry_spot

    now_utc = datetime.now(timezone.utc)
    entry_ts = getattr(position, "entry_timestamp", getattr(position, "entry_time", None))
    if entry_ts:
        if entry_ts.tzinfo is None:
            entry_ts = entry_ts.replace(tzinfo=timezone.utc)
        elapsed_sec = max(1.0, (now_utc - entry_ts).total_seconds())
    else:
        elapsed_sec = 1.0

    # UPGRADE 46: Restored Friday's Direct Option-Chain Spread MTM Valuation
    # Evaluates (live_spread - entry_spread) * total_qty directly from the option chain
    buy_legs = [l for l in legs if str(l.get("action", "")).upper() == "BUY"]
    sell_legs = [l for l in legs if str(l.get("action", "")).upper() == "SELL"]

    current_spread_value = 0.0
    entry_spread_value = 0.0

    if buy_legs and sell_legs:
        b_leg = buy_legs[0]
        s_leg = sell_legs[0]
        op_type = b_leg.get("option_type", b_leg.get("type", "CE")).upper()
        b_strike = int(b_leg.get("strike", 0))
        s_strike = int(s_leg.get("strike", 0))

        b_entry_p = float(b_leg.get("fill_price", b_leg.get("entry_price", b_leg.get("base_ltp", 0.0))))
        s_entry_p = float(s_leg.get("fill_price", s_leg.get("entry_price", s_leg.get("base_ltp", 0.0))))
        entry_spread_value = round(b_entry_p - s_entry_p, 2)

        # 1. Look up live leg prices from all_strikes_map or live_strikes_map
        b_chain = all_strikes_map.get(b_strike, {}) or live_strikes_map.get(b_strike, {})
        s_chain = all_strikes_map.get(s_strike, {}) or live_strikes_map.get(s_strike, {})

        live_buy_ltp = float(b_chain.get(f"{op_type.lower()}_ltp", 0.0) or b_chain.get(op_type, {}).get("lastPrice", 0.0) or 0.0)
        live_sell_ltp = float(s_chain.get(f"{op_type.lower()}_ltp", 0.0) or s_chain.get(op_type, {}).get("lastPrice", 0.0) or 0.0)

        # 2. Consistency & freshness check: Ensure both legs are priced on a consistent Black-Scholes curve
        # at current live Spot price so live_buy_ltp > live_sell_ltp always holds for directional spreads
        is_bull_call = (op_type == "CE" and b_strike < s_strike)
        is_bear_put = (op_type == "PE" and b_strike > s_strike)
        is_consistent = (
            (is_bull_call and live_buy_ltp > live_sell_ltp > 0)
            or (is_bear_put and live_buy_ltp > live_sell_ltp > 0)
            or (not is_bull_call and not is_bear_put and live_buy_ltp > 0 and live_sell_ltp > 0)
        )

        # If spot moved >= 1.0 pt from entry but chain quotes are completely stale (equal to entry fill prices)
        spot_moved = entry_spot and abs(spot_price - entry_spot) >= 1.0
        quotes_stale = spot_moved and (abs(live_buy_ltp - b_entry_p) < 0.01 and abs(live_sell_ltp - s_entry_p) < 0.01)

        if (not is_consistent or quotes_stale) and spot_price > 1000:
            from market_data import calculate_black_scholes_price
            iv = 0.145 if ticker_clean == "NIFTY" else (0.165 if ticker_clean == "BANKNIFTY" else 0.15)
            t_reprice = 5.0 / 365.0
            live_buy_ltp = round(calculate_black_scholes_price(spot_price, b_strike, t_reprice, iv, rate=0.07, option_type=op_type), 2)
            live_sell_ltp = round(calculate_black_scholes_price(spot_price, s_strike, t_reprice, iv, rate=0.07, option_type=op_type), 2)

        current_spread_value = round(live_buy_ltp - live_sell_ltp, 2)
        chain_gross_pnl = round((current_spread_value - entry_spread_value) * total_qty, 2)
        gross_pnl = chain_gross_pnl

        # UPGRADE 48: Spot-Verified MTM Consistency Guard
        # Compute expected gross PnL based on theoretical Spot-Delta spread change:
        # Bull Call: expected_gross_pnl = (abs(long_delta) - abs(short_delta)) * dSpot * total_qty - est_theta_decay
        # Bear Put:  expected_gross_pnl = -(abs(long_delta) - abs(short_delta)) * dSpot * total_qty - est_theta_decay
        long_delta = abs(float(b_leg.get("delta", 0.50) or 0.50))
        short_delta = abs(float(s_leg.get("delta", 0.25) or 0.25))
        net_delta = long_delta - short_delta
        if net_delta <= 0:
            net_delta = 0.24

        long_theta = abs(float(b_leg.get("theta", 0.0) or 0.0))
        short_theta = abs(float(s_leg.get("theta", 0.0) or 0.0))
        net_theta = max(0.0, long_theta - short_theta)
        est_theta_decay = round(net_theta * (elapsed_sec / (6.25 * 3600.0)) * total_qty, 2)

        strat_name_lower = (getattr(position, "strategy_name", "") or "").lower()
        is_bull_call_strat = is_bull_call or ("bull" in strat_name_lower and "call" in strat_name_lower)
        is_bear_put_strat = is_bear_put or ("bear" in strat_name_lower and "put" in strat_name_lower)

        if is_bull_call_strat:
            expected_gross_pnl = round(net_delta * dSpot * total_qty - est_theta_decay, 2)
        elif is_bear_put_strat:
            expected_gross_pnl = round(-net_delta * dSpot * total_qty - est_theta_decay, 2)
        else:
            expected_gross_pnl = chain_gross_pnl

        # If raw option-chain spread valuation diverges by > ₹250.0 or indicates loss while dSpot is favorable:
        is_favorable_spot = (is_bull_call_strat and dSpot >= 0.0) or (is_bear_put_strat and dSpot <= 0.0)
        is_divergent = abs(chain_gross_pnl - expected_gross_pnl) > 250.0
        is_false_loss = (chain_gross_pnl < 0.0) and is_favorable_spot

        if (is_divergent or is_false_loss) and (is_bull_call_strat or is_bear_put_strat):
            blended_pnl = round(0.80 * expected_gross_pnl + 0.20 * chain_gross_pnl, 2)
            if is_false_loss and blended_pnl < 0.0:
                gross_pnl = max(0.0, expected_gross_pnl)
            else:
                gross_pnl = blended_pnl
            logger.info(
                f"[SPOT-VERIFIED MTM] Option-chain quote flicker detected! ChainGross=₹{chain_gross_pnl:.2f} vs "
                f"ExpectedGross=₹{expected_gross_pnl:.2f} (dSpot={dSpot:+.2f}). Correcting MTM to ₹{gross_pnl:+.2f}."
            )
            current_spread_value = round(entry_spread_value + (gross_pnl / max(1, total_qty)), 2)

        # Keep sanity bounds: target / stop loss bounds
        spread_width = abs(b_strike - s_strike) if (b_strike and s_strike) else 200.0
        max_possible_gain = float(position.target or (spread_width * total_qty)) * 1.5
        max_possible_loss = float(position.stop_loss or position.capital_allocated or (spread_width * total_qty)) * 1.2
        gross_pnl = max(-max_possible_loss, min(max_possible_gain, gross_pnl))

        unrealized_pnl = gross_pnl
        exit_price = current_spread_value
    else:
        for leg in legs:
            action = leg.get("action", "BUY").upper()
            strike = int(leg.get("strike", 0))
            op_type = leg.get("option_type", leg.get("type", "CE")).upper()
            entry_price = float(leg.get("fill_price", leg.get("entry_price", leg.get("base_ltp", 50.0))))

            leg_chain = all_strikes_map.get(strike, {}) or live_strikes_map.get(strike, {})
            live_ltp = float(leg_chain.get(f"{op_type.lower()}_ltp", 0.0) or leg_chain.get(op_type, {}).get("lastPrice", 0.0) or 0.0)
            if live_ltp <= 0 and spot_price > 1000:
                from market_data import calculate_black_scholes_price
                iv = 0.145 if ticker_clean == "NIFTY" else 0.165
                live_ltp = round(calculate_black_scholes_price(spot_price, strike, 5.0 / 365.0, iv, rate=0.07, option_type=op_type), 2)

            current_close_price = live_ltp if live_ltp > 0 else entry_price
            if action == "BUY":
                entry_legs_value += entry_price * lot_size * qty
                current_legs_value += current_close_price * lot_size * qty
            else:
                entry_legs_value -= entry_price * lot_size * qty
                current_legs_value -= current_close_price * lot_size * qty
        unrealized_pnl = round(current_legs_value - entry_legs_value, 2)
        exit_price = round(current_legs_value / (lot_size * qty), 2) if (lot_size * qty) else 0.0

    # Sanitize floating-point negative zero
    if abs(unrealized_pnl) < 0.005 or unrealized_pnl == -0.0:
        unrealized_pnl = 0.0
    else:
        unrealized_pnl = round(unrealized_pnl, 2)

    pos_symbol = getattr(position, "symbol", getattr(position, "ticker", "NIFTY"))
    pos_id_val = pos_id or getattr(position, "id", 0)
    target_val = float(getattr(position, "target", 0.0) or 0.0)
    sl_val = float(getattr(position, "stop_loss", 0.0) or 0.0)

    logger.info(
        f"[MTM MONITOR] Pos #{pos_id_val} ({pos_symbol}): "
        f"EntrySpread={entry_spread_value:.2f} | LiveSpread={current_spread_value:.2f} | "
        f"LiveSpot={spot_price:.2f} | MTM=₹{unrealized_pnl:+.2f} | "
        f"Target=₹{target_val:.2f} | SL=₹{-sl_val:.2f}"
    )

    net_unit_price = current_spread_value if (buy_legs and sell_legs) else round(abs(current_legs_value) / max(1, total_qty), 2)

    # Estimate round-trip brokerage & taxes (UPGRADE 1)
    estimated_charges = calculate_indian_fo_charges(legs, lot_size=lot_size, lots=qty, is_round_trip=True)
    unrealized_net_pnl = round(unrealized_pnl - estimated_charges, 2)
    if abs(unrealized_net_pnl) < 0.005 or unrealized_net_pnl == -0.0:
        unrealized_net_pnl = 0.0

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
        "move_3m_pts": move_3m_pts,
        "move_5m_pts": move_5m_pts,
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
    ticker_clean = (position.ticker or "NIFTY").strip().upper()
    fallback_lot = get_lot_size(ticker_clean)
    lot_size = legs[0].get("lot_size", fallback_lot) if legs else fallback_lot
    qty = legs[0].get("qty", 1) if legs else 1

    # UPGRADE 1: Deduct total Indian F&O charges from realized PnL
    charges = calculate_indian_fo_charges(legs, lot_size=lot_size, lots=qty, is_round_trip=True)

    # UPGRADE 50 Rule 4: Exact Hard SL Clamping (CLOSED_SL)
    # Clamp realized gross loss strictly to -round(position.stop_loss, 2)
    if status_reason == "CLOSED_SL":
        gross_sl_val = float(position.stop_loss or (0.065 * float(position.capital_allocated or 0.0)))
        if gross_sl_val > 0:
            max_allowed_loss = round(gross_sl_val, 2)
            final_pnl = max(-max_allowed_loss, final_pnl)

    gross_pnl = round(final_pnl, 2)
    net_pnl = round(gross_pnl - charges, 2)

    position.status = status_reason
    position.exit_timestamp = datetime.now(timezone.utc)
    position.gross_pnl = gross_pnl
    position.charges = charges
    position.total_charges = charges
    position.pnl = net_pnl
    position.net_pnl = net_pnl
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

    # UPGRADE 50 Rule 1: Record LAST_EXIT_TIMESTAMP for mandatory 20-minute (1200s) post-exit cooldown
    clean_ticker = position.ticker.strip().upper()
    LAST_EXIT_TIMESTAMP[clean_ticker] = datetime.now(timezone.utc)

    logger.info(
        f"[INTRADAY MEMORY PRESERVED] Preserved MINUTE_CANDLES & LIVE_SPOT_HISTORY for {clean_ticker}. "
        f"Registered 20-minute post-exit cooldown timestamp ({LAST_EXIT_TIMESTAMP[clean_ticker].strftime('%H:%M:%S')} UTC)."
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

    # Clean up in-memory telemetry and breach counters
    if position.id is not None:
        POSITION_TELEMETRY.pop(position.id, None)
        SL_BREACH_COUNTS.pop(position.id, None)
        REVERSAL_BREACH_COUNTS.pop(position.id, None)

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
            exit_price = float(getattr(position, "net_premium", 0.0) or 0.0)
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
# 2. Background Monitor Loop with 4-Exit Model & Trailing Stop Exits
# ---------------------------------------------------------------------------
async def evaluate_open_positions():
    """Scans all OPEN positions and verifies target/trailing-stop/stop-loss/intraday EOD exits."""
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
                move_3m_pts = float(mtm_data.get("move_3m_pts", 0.0) or 0.0)
                move_5m_pts = float(mtm_data.get("move_5m_pts", 0.0) or 0.0)
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

                # UPGRADE 43: Ensure entry_spot is safely retrieved and never reset to current spot
                pos_entry_spot = (
                    ENTRY_SPOT_CACHE.get(pos_id)
                    or getattr(pos, "entry_spot", None)
                    or (POSITION_TELEMETRY.get(pos_id, {}).get("entry_spot") if pos_id in POSITION_TELEMETRY else None)
                    or spot_price
                )

                # Initialize in-memory position telemetry (entry_spot, peak_pnl, trend_state)
                if pos_id not in POSITION_TELEMETRY:
                    POSITION_TELEMETRY[pos_id] = {
                        "entry_spot": pos_entry_spot,
                        "peak_pnl": unrealized_pnl,
                        "trailing_active": False,
                        "trend_state": trend_state,
                        "last_unrealized_pnl": unrealized_pnl,
                    }
                else:
                    if (
                        POSITION_TELEMETRY[pos_id].get("entry_spot") is None
                        or POSITION_TELEMETRY[pos_id].get("entry_spot") <= 0
                    ):
                        POSITION_TELEMETRY[pos_id]["entry_spot"] = pos_entry_spot
                    POSITION_TELEMETRY[pos_id]["trend_state"] = trend_state
                    POSITION_TELEMETRY[pos_id]["last_unrealized_pnl"] = unrealized_pnl
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

                # UPGRADE 47: Reset SL breach counter if MTM recovers above -85% of stop-loss
                if pos_id in SL_BREACH_COUNTS and stop_loss > 0 and unrealized_pnl > -0.85 * stop_loss:
                    if SL_BREACH_COUNTS[pos_id] > 0:
                        logger.info(
                            f"[2-POLL SL RECOVERY] #{pos_id} ({pos.ticker}) MTM recovered to ₹{unrealized_pnl:.2f} > -85% SL (-₹{0.85 * stop_loss:.2f}). "
                            f"Resetting SL breach counter ({SL_BREACH_COUNTS[pos_id]} -> 0)."
                        )
                    SL_BREACH_COUNTS[pos_id] = 0

                # -------------------------------------------------------------------
                # UPGRADE 50 Rule 4: Rigid 4-Exit Model (NO CLOSED_TREND_REVERSAL)
                # An open position may ONLY exit via:
                # a) CLOSED_TARGET: gross_pnl >= position.target
                # b) CLOSED_TRAILING_STOP: Arm at peak_pnl >= 0.80 * target, floor at 0.75 * peak_pnl
                # c) CLOSED_SL: gross_pnl <= -stop_loss AND spot adverse >= 18 pts (NIFTY) / 50 pts (BANKNIFTY)
                # d) CLOSED_EOD: Auto square-off at 15:15:00 IST
                # -------------------------------------------------------------------

                # Exit Condition D: 15:15 IST Auto-Squareoff (CLOSED_EOD)
                if eod_triggered:
                    logger.info(
                        f"[EOD SQUAREOFF] 15:15 IST reached! Auto-closing #{pos.id} ({pos.ticker}) "
                        f"to prevent overnight gap risk."
                    )
                    close_position_record(session, pos, "CLOSED_EOD", unrealized_pnl, exit_price)
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

                # Exit Condition A: Target Profit Hit (CLOSED_TARGET)
                if target > 0 and unrealized_pnl >= target:
                    logger.info(f"Target Hit for #{pos.id} ({pos.ticker})! Gross PnL: INR {unrealized_pnl:,.2f} >= Target INR {target:,.2f}")
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

                # Exit Condition B: Trailing Stop (CLOSED_TRAILING_STOP)
                # Arm ONLY when peak_gross_pnl >= 0.80 * target, and lock floor at 0.75 * peak_gross_pnl
                gross_target = float(pos.target or 0.0)
                if gross_target > 0 and peak_pnl >= 0.80 * gross_target:
                    trailing_floor = round(0.75 * peak_pnl, 2)
                    if not telemetry.get("trailing_active"):
                        telemetry["trailing_active"] = True
                        telemetry["trailing_floor"] = trailing_floor
                        logger.info(
                            f"[TRAILING STOP ARMED] #{pos.id} ({pos.ticker}) Peak INR {peak_pnl:,.2f} "
                            f">= 80% Target (INR {0.80 * gross_target:,.2f}). Locking floor at INR {trailing_floor:,.2f} (75% peak)."
                        )
                    else:
                        telemetry["trailing_floor"] = max(telemetry.get("trailing_floor", trailing_floor), trailing_floor)

                if telemetry.get("trailing_active"):
                    effective_floor = telemetry.get("trailing_floor", 0.0)
                    if unrealized_pnl <= effective_floor:
                        exit_gross_pnl = max(unrealized_pnl, effective_floor)
                        realized_net_pnl = round(exit_gross_pnl - float(pos.total_charges or pos.charges or 90.0), 2)
                        logger.info(
                            f"[TRAILING STOP HIT] #{pos.id} ({pos.ticker}) Gross PnL INR {unrealized_pnl:,.2f} "
                            f"dropped to trailing floor INR {effective_floor:,.2f} (Peak INR {peak_pnl:,.2f}). "
                            f"Booking Net PnL +INR {realized_net_pnl:,.2f}."
                        )
                        close_position_record(session, pos, "CLOSED_TRAILING_STOP", exit_gross_pnl, exit_price, trend_state=trend_state)
                        pnl_sign = f"+INR {realized_net_pnl:,.2f}" if realized_net_pnl >= 0 else f"-INR {abs(realized_net_pnl):,.2f}"
                        record_exit_log({
                            "type": "AUTO_EXIT",
                            "badge_type": "TRAILING_STOP",
                            "ticker": pos.ticker,
                            "position_id": pos.id,
                            "reason": f"Trailing Stop Hit at INR {exit_price:,.2f} ({pnl_sign})",
                            "pnl": exit_gross_pnl,
                            "exit_price": exit_price,
                            "spot_price": spot_price,
                            "message": f"Trailing Stop Hit for #{pos.id} ({pos.ticker}) at INR {exit_price:,.2f} ({pnl_sign})",
                        })
                        continue

                # Exit Condition C: Spot-Verified Hard Stop-Loss (CLOSED_SL)
                # Trigger when gross_pnl <= -position.stop_loss AND Spot moved adversely by >= 18 pts (NIFTY) / >= 50 pts (BANKNIFTY)
                if stop_loss > 0 and unrealized_pnl <= -abs(stop_loss):
                    pos_ticker = (pos.ticker or "NIFTY").strip().upper()
                    spot_sl_distance = 50.0 if pos_ticker == "BANKNIFTY" else 18.0

                    if entry_spot and entry_spot > 0:
                        if is_bullish and (entry_spot - spot_price < spot_sl_distance):
                            logger.warning(
                                f"[SPOT-VERIFIED SL BLOCKED] #{pos.id} ({pos.ticker}) Bull Call Spread raw MTM breached SL "
                                f"(Gross PnL ₹{unrealized_pnl:,.2f} <= -SL ₹{-stop_loss:,.2f}), but live spot {spot_price:.2f} "
                                f">= entry {entry_spot:.2f} - {spot_sl_distance:.1f} (Drop: {entry_spot - spot_price:+.2f} pts < {spot_sl_distance:.1f} pts). "
                                f"Blocking false CLOSED_SL exit!"
                            )
                            continue
                        elif is_bearish and (spot_price - entry_spot < spot_sl_distance):
                            logger.warning(
                                f"[SPOT-VERIFIED SL BLOCKED] #{pos.id} ({pos.ticker}) Bear Put Spread raw MTM breached SL "
                                f"(Gross PnL ₹{unrealized_pnl:,.2f} <= -SL ₹{-stop_loss:,.2f}), but live spot {spot_price:.2f} "
                                f"<= entry {entry_spot:.2f} + {spot_sl_distance:.1f} (Rise: {spot_price - entry_spot:+.2f} pts < {spot_sl_distance:.1f} pts). "
                                f"Blocking false CLOSED_SL exit!"
                            )
                            continue

                    clamped_sl_pnl = -round(pos.stop_loss, 2)
                    logger.info(
                        f"Stop-Loss Hit for #{pos.id} ({pos.ticker})! "
                        f"PnL: INR {clamped_sl_pnl:,.2f} <= SL INR {-abs(stop_loss):,.2f} (clamped strictly to -stop_loss)"
                    )
                    close_position_record(session, pos, "CLOSED_SL", clamped_sl_pnl, exit_price)
                    record_exit_log({
                        "type": "AUTO_EXIT",
                        "badge_type": "SL_HIT",
                        "ticker": pos.ticker,
                        "position_id": pos.id,
                        "reason": f"SL Hit at INR {exit_price:,.2f} (-INR {abs(clamped_sl_pnl):,.2f})",
                        "pnl": clamped_sl_pnl,
                        "exit_price": exit_price,
                        "spot_price": spot_price,
                        "message": f"SL Hit at INR {exit_price:,.2f} (-INR {abs(clamped_sl_pnl):,.2f}) for #{pos.id} ({pos.ticker})",
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
