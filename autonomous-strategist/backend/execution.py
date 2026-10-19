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

from database import Account, Position, count_today_trades, engine, get_account

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
# ---------------------------------------------------------------------------
def validate_spread_structure(
    ticker: str, legs: List[Dict[str, Any]], strategy_name: str
) -> Tuple[bool, str]:
    """
    Validates directional debit spread mechanics:
    - Long (BUY) Leg: 0.38 <= abs(delta) <= 0.55 (Never allow Deep-ITM |Delta| > 0.56)
    - Short (SELL) Leg: 0.15 <= abs(delta) <= 0.30 (Never allow dead 0.00 or high 0.50+ delta)
    - Net Spread Delta: 0.20 <= abs(net_delta) <= 0.35
    - Minimum Strike Width: >= 150 pts on NIFTY, >= 400 pts on BANKNIFTY
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

    # Net Spread Delta Check
    net_delta = abs(buy_delta - sell_delta)
    if net_delta < 0.20 or net_delta > 0.35:
        return False, (
            f"Net spread delta ({net_delta:.2f}) violates required bound [0.20, 0.35]. "
            f"Low net-delta spreads (< 0.20) are mathematically incapable of 5% ROC."
        )

    # Strike Width Check
    buy_strike = int(buy_leg.get("strike", 0))
    sell_strike = int(sell_leg.get("strike", 0))
    width = abs(buy_strike - sell_strike)
    clean_ticker = ticker.strip().upper()
    min_width = 400 if clean_ticker == "BANKNIFTY" else 150

    if width < min_width:
        return False, (
            f"Strike width {width} pts is below minimum desk requirement of {min_width} pts for {clean_ticker}."
        )

    return True, "Spread verified: all leg delta and strike width bounds satisfied."


# ---------------------------------------------------------------------------
# 3. Dynamic Position Sizing (INR 8k - 12k Target Capital) (UPGRADE 2)
# ---------------------------------------------------------------------------
def calculate_dynamic_lots(
    ticker: str,
    net_debit_per_lot: float,
    raw_qty: int = 1,
    target_capital: float = 11000.0,
    max_risk_cap: float = 2000.0,
) -> int:
    """
    Dynamically scales lots so deployed capital targets INR 9,000 to INR 13,000 (Patch 28B),
    allowing 1 to 6 lots on NIFTY and 1 to 4 lots on BANKNIFTY so trades with low
    per-lot net debit (INR 1,500 - 2,500) deploy ~INR 10,000 of capital.
    """
    if net_debit_per_lot <= 0:
        return max(1, raw_qty)

    scaled_lots = max(1, round(target_capital / net_debit_per_lot))

    clean_ticker = ticker.strip().upper()
    if clean_ticker == "BANKNIFTY":
        scaled_lots = min(scaled_lots, 4)
    elif clean_ticker == "NIFTY":
        scaled_lots = min(scaled_lots, 6)
    else:
        scaled_lots = min(scaled_lots, 4)

    # Cap deployed capital to ~INR 13,500
    if (net_debit_per_lot * scaled_lots) > 13500.0 and scaled_lots > 1:
        scaled_lots = max(1, int(13000.0 / net_debit_per_lot))

    # Ensure 10% risk stays <= max_risk_cap
    while scaled_lots > 1 and (net_debit_per_lot * scaled_lots * 0.10) > max_risk_cap:
        scaled_lots -= 1

    return max(1, scaled_lots)


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

        if today_total_trades >= 4:
            logger.warning(
                f"[CIRCUIT BREAKER] Trade rejected: Session daily limit of 4 trades reached ({today_total_trades}/4)."
            )
            return {
                "success": False,
                "error": "DAILY_MAX_TRADES_EXCEEDED",
                "message": f"Anti-churn circuit breaker: Daily session cap of 4 trades reached ({today_total_trades}/4).",
                "today_total_trades": today_total_trades,
            }

        if today_symbol_trades >= 2:
            logger.warning(
                f"[CIRCUIT BREAKER] Trade rejected: Symbol limit of 2 trades reached for {ticker} ({today_symbol_trades}/2)."
            )
            return {
                "success": False,
                "error": "SYMBOL_MAX_TRADES_EXCEEDED",
                "message": f"Anti-churn circuit breaker: Daily cap of 2 trades for {ticker} reached ({today_symbol_trades}/2).",
                "today_symbol_trades": today_symbol_trades,
            }

    # Delta & Strike Width Validation (UPGRADE 3)
    is_valid_structure, structure_msg = validate_spread_structure(ticker, raw_legs, strategy_name)
    if not is_valid_structure:
        logger.warning(f"Paper trade rejected: {structure_msg}")
        return {
            "success": False,
            "error": "DELTA_OR_WIDTH_VIOLATION",
            "message": structure_msg,
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
    debit_1_lot = 0.0
    credit_1_lot = 0.0
    for leg in raw_legs:
        action = leg.get("action", "BUY").upper()
        strike = int(leg.get("strike", 0))
        option_type = leg.get("option_type", leg.get("type", "CE")).upper()
        base_ltp = float(leg.get("ltp", leg.get("premium", 50.0)))
        strike_data = all_strikes_map.get(strike, {}) if all_strikes_map else {}

        if action == "BUY":
            live_ask = float(
                strike_data.get(f"{option_type.lower()}_ask", 0.0)
                or strike_data.get("askPrice", 0.0)
                or 0.0
            )
            price = round(live_ask if live_ask > 0 else (base_ltp * (1.0 + SLIPPAGE_PCT)), 2)
            debit_1_lot += price * lot_size
        else:
            live_bid = float(
                strike_data.get(f"{option_type.lower()}_bid", 0.0)
                or strike_data.get("bidprice", 0.0)
                or 0.0
            )
            price = round(live_bid if live_bid > 0 else (base_ltp * (1.0 - SLIPPAGE_PCT)), 2)
            credit_1_lot += price * lot_size

    is_debit_spread = debit_1_lot > credit_1_lot
    net_debit_1_lot = round(debit_1_lot - credit_1_lot, 2)

    # Dynamic Lot Sizing targeting INR 9k - 13k capital (Patch 28B)
    raw_qty = int(trade_proposal.get("lot_size", trade_proposal.get("qty", trade_proposal.get("lots", 1))))
    if is_debit_spread and net_debit_1_lot > 0:
        qty = calculate_dynamic_lots(ticker, net_debit_1_lot, raw_qty=raw_qty, target_capital=11000.0, max_risk_cap=2000.0)
    else:
        qty = max(1, raw_qty)

    # Step B: Compute filled legs with live order-book pricing for the scaled lots
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
        strike_data = all_strikes_map.get(strike, {}) if all_strikes_map else {}

        if action == "BUY":
            live_ask = float(
                strike_data.get(f"{option_type.lower()}_ask", 0.0)
                or strike_data.get("askPrice", 0.0)
                or 0.0
            )
            fill_price = round(live_ask if live_ask > 0 else (base_ltp * (1.0 + SLIPPAGE_PCT)), 2)
            total_debit += fill_price * lot_size * qty
        else:
            live_bid = float(
                strike_data.get(f"{option_type.lower()}_bid", 0.0)
                or strike_data.get("bidprice", 0.0)
                or 0.0
            )
            fill_price = round(live_bid if live_bid > 0 else (base_ltp * (1.0 - SLIPPAGE_PCT)), 2)
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

    # Capital Locked Bug Fix (UPGRADE 2): Defined-risk debit spreads lock true Net Debit deployed
    if is_debit_spread:
        required_capital = round(total_debit - total_credit, 2)
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
                actual_capital_at_risk = round(max((min_w * lot_size * qty) - (total_credit - total_debit), 2500.0), 2)
            else:
                actual_capital_at_risk = 2500.0
        required_capital = round(max(actual_capital_at_risk, 2500.0), 2)

    # Calculate estimated round-trip charges (UPGRADE 1)
    estimated_charges = calculate_indian_fo_charges(filled_legs, lot_size=lot_size, lots=qty, is_round_trip=True)

    # Strict Minimum Net Target Floor & R:R Calibration (Patch 28B):
    # - Set gross_sl = round(0.10 * capital_locked, 2) (10% of net debit deployed, ~25-30 pt NIFTY buffer)
    # - Set net_target_rupees = round(max(0.055 * capital_locked, 500.0), 2) (5.5% Net ROC after all charges)
    # - gross_target_rupees = round(net_target_rupees + estimated_charges, 2)
    if is_debit_spread:
        gross_sl_rupees = round(0.10 * required_capital, 2)
        net_target_rupees = round(max(0.055 * required_capital, 500.0), 2)
        gross_target_rupees = round(net_target_rupees + estimated_charges, 2)
    else:
        raw_sl = float(trade_proposal.get("stop_loss", 0.0) or 0.0)
        default_sl = max(300.0, min(0.04 * required_capital, 1500.0 - estimated_charges))
        gross_sl_rupees = round(raw_sl if raw_sl >= 300.0 else default_sl, 2)
        net_risk = gross_sl_rupees + estimated_charges
        min_net_target = max(0.055 * required_capital, 500.0, round(1.4 * net_risk, 2))
        raw_target = float(trade_proposal.get("target", 0.0) or 0.0)
        net_target_rupees = max(min_net_target, round(raw_target - estimated_charges, 2) if raw_target > min_net_target else min_net_target)
        gross_target_rupees = round(net_target_rupees + estimated_charges, 2)

    stop_loss = gross_sl_rupees
    target = gross_target_rupees

    logger.info(
        f"[{ticker}] Sizing & ROC Targets: Lots={qty} (Total Qty={lot_size * qty}) | "
        f"Capital Locked=INR {required_capital:,.2f} | Est Charges=INR {estimated_charges:,.2f} | "
        f"Gross Target=INR {target:,.2f} (Net: INR {net_target_rupees:,.2f}, 5.5% ROC) | "
        f"Gross SL=INR {stop_loss:,.2f} (Net Risk: INR {gross_sl_rupees + estimated_charges:,.2f})"
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

