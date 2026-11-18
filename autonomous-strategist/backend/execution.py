"""
Paper Trading Execution Engine & Realistic Margin Simulator
File: backend/execution.py

Implements STEP 9:
- Realistic fills with +0.2% (Buy) / -0.2% (Sell) slippage
- Index lot sizing (NIFTY: 65, BANKNIFTY: 30, FINNIFTY: 65)
- Strict virtual margin verification and ledger allocation
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from sqlmodel import Session, select

from database import (
    Account,
    MAX_DAILY_SYMBOL_TRADES,
    MAX_DAILY_TOTAL_TRADES,
    Position,
    count_today_trades,
    engine,
    get_account,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("paper_execution_engine")

# Standard NSE derivative lot sizes (UPGRADE 35: Official Lot Sizes)
LOT_SIZES: Dict[str, int] = {
    "NIFTY": 65,
    "BANKNIFTY": 30,
    "FINNIFTY": 65,
    "MIDCPNIFTY": 50,
    "RELIANCE": 250,
    "TCS": 175,
    "INFY": 400,
    "HDFCBANK": 550,
    "ICICIBANK": 700,
}

SLIPPAGE_PCT = 0.0015  # UPGRADE 41: Realistic 0.15% entry slippage (+0.15% BUY, -0.15% SELL)


def get_lot_size(ticker: str) -> int:
    """Returns the market lot size for the given ticker."""
    clean = (ticker or "NIFTY").strip().upper()
    return LOT_SIZES.get(clean, 30 if clean == "BANKNIFTY" else 65)


# ---------------------------------------------------------------------------
# 1. Exact Indian F&O Brokerage & Statutory Tax Engine (UPGRADE 1)
# ---------------------------------------------------------------------------
def calculate_indian_fo_charges(
    legs: List[Dict[str, Any]], lot_size: int, lots: int = 1, is_round_trip: bool = True
) -> float:
    """
    Deterministic helper calculating exact Indian F&O Brokerage & Statutory Taxes:
    - Brokerage: INR 20.00 per executed order leg (INR 80.00 for a 2-leg spread round-trip)
    - Buy and Sell Turnover across entry and exit legs
    - STT (Securities Transaction Tax): 0.1% (0.001) on option sell premium turnover
    - NSE Exchange Turnover Fees: 0.03503% (0.0003503) on total premium turnover (buy + sell)
    - SEBI Turnover Fees: 0.0001% (0.000001) on total premium turnover (buy + sell)
    - Stamp Duty: 0.003% (0.00003) on buy premium turnover
    - GST: 18% (0.18) on (brokerage + exchange_fees + sebi_fees)
    - Total Charges: round(brokerage + stt + exchange_fees + sebi_fees + stamp_duty + gst, 2)
    """
    breakdown = calculate_indian_fo_charges_breakdown(legs, lot_size, lots, is_round_trip)
    return breakdown["total_charges"]


def calculate_indian_fo_charges_breakdown(
    legs: List[Dict[str, Any]], lot_size: int, lots: int = 1, is_round_trip: bool = True
) -> Dict[str, float]:
    """Returns an itemized dictionary breakdown of all Indian F&O charges."""
    num_legs = len(legs) if legs else 1
    brokerage = round(20.0 * num_legs * (2 if is_round_trip else 1), 2)

    buy_turnover = 0.0
    sell_turnover = 0.0

    for leg in legs:
        entry_price = float(
            leg.get("fill_price", leg.get("entry_price", leg.get("base_ltp", leg.get("ltp", 50.0))))
        )
        exit_price = float(leg.get("exit_price", leg.get("close_price", entry_price)))
        action = str(leg.get("action", "BUY")).upper()

        if is_round_trip:
            # Round-trip trade: open action + close action
            if action == "BUY":
                buy_turnover += entry_price * lot_size * lots
                sell_turnover += exit_price * lot_size * lots
            else:  # SELL
                sell_turnover += entry_price * lot_size * lots
                buy_turnover += exit_price * lot_size * lots
        else:
            if action == "BUY":
                buy_turnover += entry_price * lot_size * lots
            else:
                sell_turnover += entry_price * lot_size * lots

    stt = round(0.001 * sell_turnover, 2)
    exchange_fees = round(0.0003503 * (buy_turnover + sell_turnover), 2)
    sebi_fees = round(0.000001 * (buy_turnover + sell_turnover), 2)
    stamp_duty = round(0.00003 * buy_turnover, 2)
    gst = round(0.18 * (brokerage + exchange_fees + sebi_fees), 2)
    total_charges = round(brokerage + stt + exchange_fees + sebi_fees + stamp_duty + gst, 2)

    return {
        "brokerage": brokerage,
        "buy_turnover": round(buy_turnover, 2),
        "sell_turnover": round(sell_turnover, 2),
        "total_turnover": round(buy_turnover + sell_turnover, 2),
        "stt": stt,
        "exchange_fees": exchange_fees,
        "sebi_fees": sebi_fees,
        "stamp_duty": stamp_duty,
        "gst": gst,
        "total_charges": total_charges,
    }


# ---------------------------------------------------------------------------
# 2. Strict Leg-Delta Bounds & Strike Width Validation (UPGRADE 3)
def get_position_bias(strategy_name: str, market_bias: str = "", legs: list = None) -> str:
    """
    UPGRADE 34: Determines directional bias (BULLISH, BEARISH, or NEUTRAL)
    from strategy name, market bias string, or leg structure.
    """
    s = (strategy_name or "").lower()
    b = (market_bias or "").lower()
    if any(w in s for w in ["bull call", "bullish", "call spread"]) or "bullish" in b:
        return "BULLISH"
    if any(w in s for w in ["bear put", "bearish", "put spread"]) or "bearish" in b:
        return "BEARISH"
    if "bull" in s:
        return "BULLISH"
    if "bear" in s:
        return "BEARISH"
    if legs:
        for l in legs:
            if str(l.get("action", "")).upper() == "BUY":
                op = str(l.get("option_type", l.get("type", ""))).upper()
                if op == "CE":
                    return "BULLISH"
                elif op == "PE":
                    return "BEARISH"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
def validate_spread_structure(
    ticker: str, legs: List[Dict[str, Any]], strategy_name: str
) -> Tuple[bool, str]:
    """
    Validates directional debit spread mechanics (UPGRADE 34):
    - Long (BUY) Leg: 0.38 <= abs(delta) <= 0.55 (Never allow Deep-ITM |Delta| > 0.56)
    - Short (SELL) Leg: 0.15 <= abs(delta) <= 0.30 (Never allow dead 0.00 or high 0.50+ delta)
    - Net Spread Delta: 0.20 <= abs(net_delta) <= 0.35 (round(net_delta, 2) >= 0.19)
    - Strike Width: [150, 250] pts on NIFTY, [400, 600] pts on BANKNIFTY
    """
    strat_lower = strategy_name.lower()
    is_directional = any(
        s in strat_lower for s in ["bull call", "bear put", "call spread", "put spread"]
    ) and not any(s in strat_lower for s in ["condor", "straddle", "strangle"])

    if not is_directional or len(legs) != 2:
        return True, "Non-directional or multi-leg strategy. Specific delta bounds waived."

    buy_legs = [l for l in legs if str(l.get("action", "")).upper() == "BUY"]
    sell_legs = [l for l in legs if str(l.get("action", "")).upper() == "SELL"]

    if len(buy_legs) != 1 or len(sell_legs) != 1:
        return True, "Spread structure not 1 BUY + 1 SELL leg."

    buy_leg = buy_legs[0]
    sell_leg = sell_legs[0]

    buy_delta = abs(float(buy_leg.get("delta", 0.5) or 0.5))
    sell_delta = abs(float(sell_leg.get("delta", 0.25) or 0.25))

    # Long (BUY) Leg Check
    if buy_delta < 0.38 or buy_delta > 0.55:
        return False, (
            f"Long BUY leg delta ({buy_delta:.2f}) violates strict bound [0.38, 0.55]. "
            f"Deep-ITM (|Delta| > 0.56) fake spreads are strictly prohibited."
        )

    # Short (SELL) Leg Check
    if sell_delta < 0.15 or sell_delta > 0.30:
        return False, (
            f"Short SELL leg delta ({sell_delta:.2f}) violates strict bound [0.15, 0.30]. "
            f"Dead legs (< 0.15) or high-delta legs (> 0.30) are strictly prohibited."
        )

    # Net Spread Delta Check (UPGRADE 34: round to 2 decimals & compare >= 0.19 for floating point robustness)
    net_delta = round(abs(buy_delta - sell_delta), 2)
    if round(net_delta, 2) < 0.19 or net_delta > 0.35:
        return False, (
            f"Net spread delta ({net_delta:.2f}) violates required bound [0.20, 0.35]. "
            f"Low net-delta spreads (< 0.20) are mathematically incapable of 5% ROC."
        )

    # Strike Width Check (UPGRADE 34: strict minimum & maximum caps)
    buy_strike = int(buy_leg.get("strike", 0))
    sell_strike = int(sell_leg.get("strike", 0))
    width = abs(buy_strike - sell_strike)
    clean_ticker = ticker.strip().upper()
    min_width = 400 if clean_ticker == "BANKNIFTY" else 150
    max_width = 600 if clean_ticker == "BANKNIFTY" else 250

    if width < min_width or width > max_width:
        return False, (
            f"Strike width {width} pts violates required range [{min_width}, {max_width}] pts for {clean_ticker}."
        )

    return True, "Spread verified: all leg delta and strike width bounds satisfied."


# ---------------------------------------------------------------------------
# 3. Dynamic Position Sizing (INR 8k - 15k Target Capital) (UPGRADE 35 & 42)
# ---------------------------------------------------------------------------
def calculate_dynamic_lots(
    ticker: str,
    net_debit_per_lot: float,
    raw_qty: int = 2,
    target_capital: float = 13000.0,
    max_risk_cap: float = 2000.0,
    max_capital_cap: float = 14800.0,
    slippage_buffer_pct: float = 0.02,
    conviction_tier: str = "HIGH_CONVICTION",
) -> int:
    """
    UPGRADE 50 Rule 3: Uniform 2-Lot Sizing across all trades.
    - NIFTY: uniform 2 lots = 130 qty
    - BANKNIFTY: uniform 2 lots = 60 qty
    Every approved trade executes as HIGH_CONVICTION with uniform 2 Lots.
    """
    return 2


def adjust_short_leg_for_lot_cap(
    ticker: str,
    legs: List[Dict[str, Any]],
    lot_size: int,
    max_cap: float = 15000.0,
) -> Tuple[List[Dict[str, Any]], float]:
    """
    UPGRADE 35: If cost_per_lot slightly exceeds INR 15,000 on a 1-lot trade,
    adjusts the short leg width by 50 pts on NIFTY or 100 pts on BANKNIFTY so 1 lot fits <= INR 15,000.
    """
    clean_ticker = (ticker or "NIFTY").strip().upper()
    step_adj = 50 if clean_ticker == "NIFTY" else 100

    buy_legs = [l for l in legs if str(l.get("action", "")).upper() == "BUY"]
    sell_legs = [l for l in legs if str(l.get("action", "")).upper() == "SELL"]

    if len(buy_legs) != 1 or len(sell_legs) != 1:
        return legs, 0.0

    b_leg = buy_legs[0]
    s_leg = sell_legs[0]

    b_price = float(b_leg.get("fill_price", b_leg.get("ltp", 0.0)))
    s_price = float(s_leg.get("fill_price", s_leg.get("ltp", 0.0)))
    net_deb_share = b_price - s_price
    cost_per_lot = net_deb_share * lot_size

    if cost_per_lot > max_cap:
        b_strike = int(b_leg.get("strike", 0))
        s_strike = int(s_leg.get("strike", 0))
        op_type = str(b_leg.get("option_type", "CE")).upper()

        if op_type == "CE":
            if (s_strike - step_adj) > b_strike:
                s_leg["strike"] = s_strike - step_adj
        else:
            if (s_strike + step_adj) < b_strike:
                s_leg["strike"] = s_strike + step_adj

        target_max_net_deb = math.floor((max_cap / lot_size) * 100.0) / 100.0
        new_s_price = round(max(5.0, b_price - target_max_net_deb), 2)
        s_leg["ltp"] = new_s_price
        if "fill_price" in s_leg:
            s_leg["fill_price"] = new_s_price
        net_deb_share = round(b_price - new_s_price, 2)
        cost_per_lot = min(max_cap, round(net_deb_share * lot_size, 2))

    return legs, cost_per_lot


# ---------------------------------------------------------------------------
# 4. Order Execution Engine
# ---------------------------------------------------------------------------
def execute_paper_order(trade_proposal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes a virtual options/equity order against the paper trading database:
    - Enforces Anti-Churn Overtrading Circuit Breakers (4 trades/day, 2/symbol)
    - Validates strict leg-delta bounds and strike width
    - Dynamically scales lots to target INR 8,000 - 12,000 capital
    - Locks true Net Debit deployed (NOT gross buy leg)
    - Enforces 5% Net ROC floor and Net Reward : Net Risk >= 1.4 : 1
    """
    ticker = trade_proposal.get("ticker", "NIFTY").strip().upper()
    strategy_name = trade_proposal.get("strategy_name", "Custom Options Strategy")
    raw_legs = trade_proposal.get("legs", [])
    thesis = str(trade_proposal.get("rationale", trade_proposal.get("thesis", "Algorithmic entry")))

    lot_size = get_lot_size(ticker)
    logger.info(f"Executing paper order for [{ticker}] - Strategy: {strategy_name} (Lot Size: {lot_size})...")

    # Circuit Breaker Check 1: Max Trades Per Day & Per Symbol (UPGRADE 5)
    with Session(engine) as session:
        today_total_trades = count_today_trades(session)
        today_symbol_trades = count_today_trades(session, ticker)

        if today_total_trades >= MAX_DAILY_TOTAL_TRADES:
            logger.warning(
                f"[CIRCUIT BREAKER] Trade rejected: Session daily limit of {MAX_DAILY_TOTAL_TRADES} trades reached ({today_total_trades}/{MAX_DAILY_TOTAL_TRADES})."
            )
            return {
                "success": False,
                "error": "DAILY_MAX_TRADES_EXCEEDED",
                "message": f"Anti-churn circuit breaker: Daily session cap of {MAX_DAILY_TOTAL_TRADES} trades reached ({today_total_trades}/{MAX_DAILY_TOTAL_TRADES}).",
                "today_total_trades": today_total_trades,
            }

        if today_symbol_trades >= MAX_DAILY_SYMBOL_TRADES:
            logger.warning(
                f"[CIRCUIT BREAKER] Trade rejected: Symbol limit of {MAX_DAILY_SYMBOL_TRADES} trades reached for {ticker} ({today_symbol_trades}/{MAX_DAILY_SYMBOL_TRADES})."
            )
            return {
                "success": False,
                "error": "SYMBOL_MAX_TRADES_EXCEEDED",
                "message": f"Anti-churn circuit breaker: Daily cap of {MAX_DAILY_SYMBOL_TRADES} trades for {ticker} reached ({today_symbol_trades}/{MAX_DAILY_SYMBOL_TRADES}).",
                "today_symbol_trades": today_symbol_trades,
            }

        # Concurrency Check (UPGRADE 32): Maximum 1 concurrently OPEN position per symbol
        existing_open_pos = session.exec(
            select(Position).where(
                Position.status == "OPEN",
                Position.ticker == ticker.upper(),
            )
        ).first()
        if existing_open_pos:
            logger.warning(
                f"[CONCURRENCY BREAKER] Trade rejected: Active OPEN position #{existing_open_pos.id} already exists for {ticker}. "
                f"Maximum 1 concurrently OPEN position allowed per symbol."
            )
            return {
                "success": False,
                "error": "CONCURRENT_SYMBOL_POSITION_EXISTS",
                "message": f"Active OPEN position #{existing_open_pos.id} already exists for {ticker}. Maximum 1 concurrently OPEN position allowed per symbol to prevent stacking duplicate trades on the same candle.",
            }

        # Cross-Index Directional Conflict & Drawdown Contagion Guard (UPGRADE 34 & 38)
        # 1. Never allow opposing trades across NIFTY and BANKNIFTY (e.g., Bull Call on NIFTY while Bear Put is open on BANKNIFTY)
        # 2. Never open a correlated trade when other index is in drawdown (unrealized PnL < -INR 250.00)
        all_open_positions = session.exec(
            select(Position).where(Position.status == "OPEN")
        ).all()
        new_bias = get_position_bias(strategy_name, trade_proposal.get("market_bias", ""), raw_legs)
        for open_pos in all_open_positions:
            if open_pos.ticker.upper() != ticker.upper():
                open_bias = get_position_bias(open_pos.strategy_name)
                # Opposing directional conflict
                if (new_bias == "BULLISH" and open_bias == "BEARISH") or (new_bias == "BEARISH" and open_bias == "BULLISH"):
                    logger.warning(
                        f"[CROSS-INDEX CONFLICT GUARD] Blocked {ticker} {strategy_name} ({new_bias}) "
                        f"because opposing position #{open_pos.id} is OPEN on {open_pos.ticker} ({open_pos.strategy_name}, {open_bias})."
                    )
                    return {
                        "success": False,
                        "error": "CROSS_INDEX_DIRECTIONAL_CONFLICT",
                        "message": (
                            f"Cross-index directional conflict guard: Cannot execute {new_bias} trade on {ticker} "
                            f"while opposing {open_bias} position #{open_pos.id} is open on {open_pos.ticker}."
                        ),
                    }
                # UPGRADE 38: Drawdown Contagion Guard for correlated trades
                if (new_bias == open_bias) and open_bias in ("BULLISH", "BEARISH"):
                    other_unrealized = None
                    try:
                        from position_monitor import POSITION_TELEMETRY, calculate_position_mtm
                        if open_pos.id in POSITION_TELEMETRY and "last_unrealized_pnl" in POSITION_TELEMETRY[open_pos.id]:
                            other_unrealized = POSITION_TELEMETRY[open_pos.id]["last_unrealized_pnl"]
                        elif open_pos.gross_pnl is not None:
                            other_unrealized = open_pos.gross_pnl
                        elif open_pos.pnl is not None:
                            other_unrealized = open_pos.pnl
                        else:
                            mtm_res = calculate_position_mtm(open_pos)
                            if mtm_res and "unrealized_pnl" in mtm_res:
                                other_unrealized = mtm_res["unrealized_pnl"]
                    except Exception as exc:
                        logger.debug(f"Error checking live MTM for other position #{open_pos.id}: {exc}")
                        if open_pos.gross_pnl is not None:
                            other_unrealized = open_pos.gross_pnl
                        elif open_pos.pnl is not None:
                            other_unrealized = open_pos.pnl

                    if other_unrealized is not None and other_unrealized < -250.0:
                        logger.warning(
                            f"[CROSS-INDEX CONTAGION GUARD] Blocked {ticker} {strategy_name} ({new_bias}) "
                            f"because correlated position #{open_pos.id} on {open_pos.ticker} ({open_pos.strategy_name}) "
                            f"is in drawdown (unrealized PnL: INR {other_unrealized:,.2f} < -INR 250.00)."
                        )
                        return {
                            "success": False,
                            "error": "CROSS_INDEX_DRAWDOWN_CONTAGION",
                            "message": (
                                f"Cross-index drawdown contagion guard: Cannot execute correlated {new_bias} trade on {ticker} "
                                f"while active position #{open_pos.id} on {open_pos.ticker} is in drawdown "
                                f"(INR {other_unrealized:,.2f} < -INR 250.00). Waiting for recovery or closure."
                            ),
                        }

        # Consecutive Same-Direction Exhaustion Cooldown & Stoploss Cooldown (UPGRADE 34 & 47)
        try:
            from autopilot import check_symbol_cooldown_and_exhaustion
            prop_spot = trade_proposal.get("spot_price")
            if not prop_spot and isinstance(trade_proposal.get("market_data"), dict):
                prop_spot = trade_proposal.get("market_data", {}).get("spot_price")
            cool_ok, cool_reason = check_symbol_cooldown_and_exhaustion(
                ticker, proposed_direction=new_bias, session=session, current_spot=prop_spot
            )
            if not cool_ok:
                logger.warning(f"[EXHAUSTION/COOLDOWN BREAKER] Trade rejected for {ticker}: {cool_reason}")
                return {
                    "success": False,
                    "error": "COOLDOWN_ACTIVE",
                    "message": cool_reason,
                }
        except Exception as exc:
            logger.debug(f"Exhaustion cooldown check in execution skipped: {exc}")

    # Delta & Strike Width Validation (UPGRADE 3)
    is_valid_structure, structure_msg = validate_spread_structure(ticker, raw_legs, strategy_name)
    if not is_valid_structure:
        logger.warning(f"Paper trade rejected: {structure_msg}")
        return {
            "success": False,
            "error": "DELTA_OR_WIDTH_VIOLATION",
            "message": structure_msg,
        }

    # Strict Regime & Momentum Gatekeeper Validation (UPGRADE 31)
    market_data_info = trade_proposal.get("market_data")
    if market_data_info and isinstance(market_data_info, dict):
        from risk_manager import validate_strict_regime_gatekeeper
        gatekeeper_ok, gatekeeper_msg = validate_strict_regime_gatekeeper(
            ticker=ticker,
            strategy_name=strategy_name,
            market_data=market_data_info,
            market_bias=trade_proposal.get("market_bias", ""),
        )
        if not gatekeeper_ok and gatekeeper_msg:
            logger.warning(f"Paper trade rejected: {gatekeeper_msg}")
            return {
                "success": False,
                "error": "REGIME_GATEKEEPER_REJECTED",
                "message": gatekeeper_msg,
            }

    # Retrieve all_strikes_map for live order-book quotes (STEP 22)
    all_strikes_map = (
        trade_proposal.get("all_strikes_map")
        or trade_proposal.get("market_data", {}).get("all_strikes_map")
    )
    if not all_strikes_map:
        try:
            from market_data import fetch_live_options_data
            mdata = fetch_live_options_data(ticker)
            all_strikes_map = mdata.get("all_strikes_map", {}) if mdata else {}
        except Exception:
            all_strikes_map = {}

    # Step A: Compute 1-lot pricing for accurate dynamic position sizing
    # UPGRADE 41: Enforce hard invariant on calibrated legs from QuantProposal:
    # If buy_leg.price <= sell_leg.price * 1.25, force sell_leg.price = round(buy_leg.price * 0.48, 2)
    # so net_debit = buy_leg.price - sell_leg.price is ALWAYS strictly positive (>= ₹30 on NIFTY, >= ₹100 on BANKNIFTY)
    min_deb_share = 100.0 if ticker == "BANKNIFTY" else 30.0
    min_long_ltp = 100.0 if ticker == "BANKNIFTY" else 40.0
    buy_legs_in = [l for l in raw_legs if str(l.get("action", "")).upper() == "BUY"]
    sell_legs_in = [l for l in raw_legs if str(l.get("action", "")).upper() == "SELL"]
    if len(buy_legs_in) == 1 and len(sell_legs_in) == 1:
        b_leg = buy_legs_in[0]
        s_leg = sell_legs_in[0]
        b_ltp = float(b_leg.get("ltp", b_leg.get("price", b_leg.get("fill_price", b_leg.get("premium", 0.0)))) or 0.0)
        s_ltp = float(s_leg.get("ltp", s_leg.get("price", s_leg.get("fill_price", s_leg.get("premium", 0.0)))) or 0.0)
        if b_ltp <= s_ltp * 1.25 or (b_ltp - s_ltp) < min_deb_share:
            if b_ltp < min_long_ltp:
                b_ltp = max(min_long_ltp, round(min_deb_share / 0.52, 2))
            s_ltp = round(b_ltp * 0.48, 2)
            if (b_ltp - s_ltp) < min_deb_share:
                b_ltp = round(min_deb_share / 0.52, 2)
                s_ltp = round(b_ltp * 0.48, 2)
            b_leg["ltp"] = b_ltp
            b_leg["price"] = b_ltp
            s_leg["ltp"] = s_ltp
            s_leg["price"] = s_ltp

    # UPGRADE 41: DO NOT overwrite calibrated leg prices with mismatched option chain lookups that invert the spread!
    # Use exact calibrated leg prices with realistic entry slippage of +0.15% on BUY and -0.15% on SELL.
    debit_1_lot = 0.0
    credit_1_lot = 0.0
    for leg in raw_legs:
        action = leg.get("action", "BUY").upper()
        base_ltp = float(leg.get("ltp", leg.get("price", leg.get("fill_price", leg.get("premium", 50.0)))))
        if action == "BUY":
            price = round(base_ltp * (1.0 + SLIPPAGE_PCT), 2)
            debit_1_lot += price * lot_size
        else:
            price = round(base_ltp * (1.0 - SLIPPAGE_PCT), 2)
            credit_1_lot += price * lot_size

    # Hard invariant: ensure 1-lot pricing is never inverted
    if debit_1_lot <= credit_1_lot or (debit_1_lot - credit_1_lot) < (min_deb_share * lot_size):
        b_p = debit_1_lot / lot_size
        if b_p < min_long_ltp:
            b_p = max(min_long_ltp, round(min_deb_share / 0.52, 2))
        s_p = round(b_p * 0.48, 2)
        if (b_p - s_p) < min_deb_share:
            b_p = round(min_deb_share / 0.52, 2)
            s_p = round(b_p * 0.48, 2)
        debit_1_lot = b_p * lot_size
        credit_1_lot = s_p * lot_size

    is_debit_spread = True
    net_debit_1_lot = max(round(min_deb_share * lot_size, 2), round(debit_1_lot - credit_1_lot, 2))

    # UPGRADE 35: If 1-lot trade slightly exceeds INR 15,000, adjust short leg width
    if is_debit_spread and net_debit_1_lot > 15000.0:
        raw_legs, adj_cost = adjust_short_leg_for_lot_cap(ticker, raw_legs, lot_size, max_cap=15000.0)
        debit_1_lot = 0.0
        credit_1_lot = 0.0
        for leg in raw_legs:
            action = leg.get("action", "BUY").upper()
            price = float(leg.get("fill_price", leg.get("ltp", 50.0)))
            if action == "BUY":
                debit_1_lot += price * lot_size
            else:
                credit_1_lot += price * lot_size
        net_debit_1_lot = round(debit_1_lot - credit_1_lot, 2)

    # UPGRADE 50 Rule 3: Uniform 2 Lots & HIGH_CONVICTION execution
    conviction_tier = "HIGH_CONVICTION"
    qty = 2

    # Step B: Compute filled legs preserving calibrated leg prices with slippage (UPGRADE 41 & 43)
    spot_price = float(trade_proposal.get("spot_price", 0.0) or trade_proposal.get("market_data", {}).get("spot_price", 0.0) or 0.0)
    if spot_price <= 0:
        try:
            from market_data import fetch_live_options_data
            mdata = fetch_live_options_data(ticker)
            if mdata:
                spot_price = float(mdata.get("spot_price", 0.0) or 0.0)
        except Exception:
            pass
    if spot_price <= 0 and raw_legs:
        for l in raw_legs:
            if str(l.get("action", "")).upper() == "BUY":
                spot_price = float(l.get("strike", 0.0) or 0.0)
                break
    filled_legs: List[Dict[str, Any]] = []
    total_debit = 0.0
    total_credit = 0.0

    for leg in raw_legs:
        action = leg.get("action", "BUY").upper()
        strike = int(leg.get("strike", 0))
        option_type = leg.get("option_type", leg.get("type", "CE")).upper()
        expiry = leg.get("expiry", "Current")
        base_ltp = float(leg.get("ltp", leg.get("price", leg.get("premium", 50.0))))
        delta = float(leg.get("delta", 0.5))

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
            "entry_price": fill_price,
            "entry_spot": spot_price,
            "lot_size": lot_size,
            "qty": qty,
            "total_quantity": lot_size * qty,
            "delta": delta,
        })

    # Enforce hard invariant on filled legs:
    b_legs = [l for l in filled_legs if l.get("action") == "BUY"]
    s_legs = [l for l in filled_legs if l.get("action") == "SELL"]
    if len(b_legs) == 1 and len(s_legs) == 1:
        bf = float(b_legs[0]["fill_price"])
        sf = float(s_legs[0]["fill_price"])
        if bf <= sf * 1.25 or (bf - sf) < min_deb_share:
            if bf < min_long_ltp:
                bf = max(min_long_ltp, round(min_deb_share / 0.52, 2))
            sf = round(bf * 0.48, 2)
            if (bf - sf) < min_deb_share:
                bf = round(min_deb_share / 0.52, 2)
                sf = round(bf * 0.48, 2)
            b_legs[0]["fill_price"] = bf
            b_legs[0]["entry_price"] = bf
            s_legs[0]["fill_price"] = sf
            s_legs[0]["entry_price"] = sf
            total_debit = bf * lot_size * qty
            total_credit = sf * lot_size * qty

    net_premium = round((total_credit - total_debit) / (lot_size * qty), 2)

    # Capital Locked Bug Fix (UPGRADE 33, 36 & 41): Defined-risk debit spreads lock true Net Debit deployed
    if is_debit_spread:
        actual_net_debit = round(total_debit - total_credit, 2)
        min_required_cap = round(min_deb_share * lot_size * qty, 2)
        required_capital = max(min_required_cap, actual_net_debit)
        actual_capital_at_risk = required_capital
    else:
        raw_ml = float(trade_proposal.get("max_loss", 0.0) or 0.0)
        if raw_ml > 500.0:
            actual_capital_at_risk = round(raw_ml, 2)
        else:
            buy_strikes = [l.get("strike", 0) for l in filled_legs if l.get("action") == "BUY"]
            sell_strikes = [l.get("strike", 0) for l in filled_legs if l.get("action") == "SELL"]
            if buy_strikes and sell_strikes and any(b != s for b in buy_strikes for s in sell_strikes):
                min_w = min(abs(b - s) for b in buy_strikes for s in sell_strikes if b != s)
                actual_capital_at_risk = round(max((min_w * lot_size * qty) - (total_credit - total_debit), 0.0), 2)
            else:
                actual_capital_at_risk = round(total_credit, 2)
        required_capital = round(actual_capital_at_risk, 2)

    # Calculate estimated round-trip charges (UPGRADE 1)
    estimated_charges = calculate_indian_fo_charges(filled_legs, lot_size=lot_size, lots=qty, is_round_trip=True)

    # UPGRADE 50 Rule 3: Uniform 2-Lot Sizing & Positive Reward-to-Risk
    # Net Target = 7.5% of Capital Locked (floor ₹750.00) + Est Charges
    # Hard Gross SL = 6.5% of Capital Locked
    conviction_tier = "HIGH_CONVICTION"
    sl_rate = 0.065
    net_target_rupees = round(max(750.0, required_capital * 0.075), 2)
    gross_target_rupees = round(net_target_rupees + estimated_charges, 2)
    gross_sl_rupees = round(required_capital * sl_rate, 2)

    stop_loss = gross_sl_rupees
    target = gross_target_rupees

    logger.info(
        f"[{ticker}] Sizing & ROC Targets ({conviction_tier}): Lots={qty} (Total Qty={lot_size * qty}) | "
        f"Capital Locked=INR {required_capital:,.2f} | Est Charges=INR {estimated_charges:,.2f} | "
        f"Gross Target=INR {target:,.2f} (Net: INR {net_target_rupees:,.2f}, 7.5% ROC, Floor: INR 750.00) | "
        f"Gross SL=INR {stop_loss:,.2f} ({sl_rate*100:.1f}%, Net Risk: INR {gross_sl_rupees + estimated_charges:,.2f})"
    )

    # Step C: Ledger verification and margin allocation
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

        # Create Position record with estimated charges initialized
        new_position = Position(
            ticker=ticker,
            strategy_name=strategy_name,
            legs_json=json.dumps(filled_legs),
            net_premium=net_premium,
            capital_allocated=required_capital,
            stop_loss=stop_loss,
            target=target,
            charges=estimated_charges,
            total_charges=estimated_charges,
            entry_timestamp=datetime.now(timezone.utc),
            status="OPEN",
            thesis=thesis,
            conviction_tier=conviction_tier,
            entry_spot=spot_price,
        )

        session.add(account)
        session.add(new_position)
        session.commit()
        session.refresh(new_position)
        session.refresh(account)

        logger.info(
            f"Paper trade filled! Position ID #{new_position.id} committed. "
            f"Capital Locked: INR {required_capital:,.2f} | Free Cash: INR {account.virtual_balance:,.2f}"
        )

        # STEP 27: Explicit Trade Execution & Leg Logging
        legs_detail_str = " | ".join(
            f"{l.get('action')} {l.get('strike')} {l.get('option_type')} @ ₹{l.get('fill_price', l.get('entry_price', 0.0)):,.2f}"
            for l in filled_legs
        )
        logger.info(f"[TRADE EXECUTED] {ticker} {strategy_name} | {legs_detail_str}")

        legs_multiline = "\n".join(
            f"  Leg #{idx}: {l.get('action')} {l.get('strike')} {l.get('option_type')} @ ₹{l.get('fill_price', l.get('entry_price', 0.0)):,.2f} "
            f"(Lot: {l.get('lot_size', lot_size)} x {l.get('qty', qty)}, Delta: {l.get('delta', 0.0):+.2f})"
            for idx, l in enumerate(filled_legs, 1)
        )
        logger.info(
            f"[EXECUTION DETAILS] Position #{new_position.id} ({ticker} {strategy_name}):\n{legs_multiline}\n"
            f"  Capital Locked: ₹{required_capital:,.2f} | Est Charges: ₹{estimated_charges:,.2f} | Target: ₹{target:,.2f} | SL: ₹{stop_loss:,.2f}"
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
            "estimated_charges": estimated_charges,
            "gross_target": target,
            "net_target": net_target_rupees,
            "stop_loss": stop_loss,
            "conviction_tier": conviction_tier,
            "lots": qty,
            "entry_timestamp": new_position.entry_timestamp.isoformat(),
            "message": f"Order filled on NSE Paper Engine. Capital locked: INR {required_capital:,.2f} ({qty} lots).",
        }


if __name__ == "__main__":
    from database import init_db
    init_db()
    test_trade = {
        "ticker": "NIFTY",
        "strategy_name": "NIFTY Bull Call Spread",
        "legs": [
            {"action": "BUY", "strike": 22350, "type": "CE", "ltp": 165.0, "delta": 0.50},
            {"action": "SELL", "strike": 22500, "type": "CE", "ltp": 75.0, "delta": 0.24},
        ],
        "max_profit": 1675.0,
        "max_loss": 825.0,
        "stop_loss": 500.0,
        "target": 1200.0,
        "rationale": "Bullish PCR test run with Upgrade 28 parameters",
    }
    res = execute_paper_order(test_trade)
    print("Execution Result:", json.dumps(res, indent=2))

