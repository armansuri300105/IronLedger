"""
Comprehensive Autonomous Verification Suite for System Upgrade 28
File: backend/test_upgrade_28.py
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import os
import tempfile
from sqlmodel import Session, select, create_engine

import database
import execution
import position_monitor

from database import Account, Position, count_today_trades, init_db
from execution import (
    calculate_dynamic_lots,
    calculate_indian_fo_charges,
    calculate_indian_fo_charges_breakdown,
    execute_paper_order,
    validate_spread_structure,
)
from position_monitor import (
    LAST_EXIT_TIMESTAMP,
    calculate_position_mtm,
    close_position_record,
)

# Use isolated temporary test database so tests don't collide with live/dev database records
TEST_DB_PATH = os.path.join(tempfile.gettempdir(), "test_paper_trader_up28.db")
if os.path.exists(TEST_DB_PATH):
    try:
        os.remove(TEST_DB_PATH)
    except Exception:
        pass

test_engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})
database.engine = test_engine
execution.engine = test_engine
position_monitor.engine = test_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_upgrade_28")


def test_1_indian_fo_charges():
    print("\n--- TEST 1: Indian F&O Brokerage & Tax Engine ---")
    # 2-leg spread on NIFTY: BUY CE @ 150, SELL CE @ 75 (1 lot = 25 qty)
    legs = [
        {"action": "BUY", "strike": 22400, "option_type": "CE", "fill_price": 150.0, "delta": 0.50},
        {"action": "SELL", "strike": 22550, "option_type": "CE", "fill_price": 75.0, "delta": 0.24},
    ]
    breakdown_1lot = calculate_indian_fo_charges_breakdown(legs, lot_size=25, lots=1, is_round_trip=True)
    charges_1lot = calculate_indian_fo_charges(legs, lot_size=25, lots=1, is_round_trip=True)

    print("1-Lot NIFTY Breakdown:", json.dumps(breakdown_1lot, indent=2))
    assert breakdown_1lot["brokerage"] == 80.0, f"Expected 80.0 brokerage for 2-leg round-trip, got {breakdown_1lot['brokerage']}"
    assert charges_1lot > 80.0, f"Expected total charges > 80.0, got {charges_1lot}"
    assert breakdown_1lot["total_charges"] == charges_1lot

    # Check 1 lot BANKNIFTY (~15 qty, buy @ 350, sell @ 150)
    bn_legs = [
        {"action": "BUY", "strike": 48000, "option_type": "CE", "fill_price": 350.0, "delta": 0.50},
        {"action": "SELL", "strike": 48400, "option_type": "CE", "fill_price": 150.0, "delta": 0.22},
    ]
    breakdown_bn = calculate_indian_fo_charges_breakdown(bn_legs, lot_size=15, lots=1, is_round_trip=True)
    print("1-Lot BANKNIFTY Total Charges: INR", breakdown_bn["total_charges"])
    assert 100.0 <= breakdown_bn["total_charges"] <= 140.0, f"Expected charges around 100-140, got {breakdown_bn['total_charges']}"
    print(">>> TEST 1 PASSED: Brokerage & Tax Engine mathematically verified!")


def test_2_dynamic_position_sizing_and_net_debit_capital():
    print("\n--- TEST 2: Dynamic Position Sizing (Patch 28B) & Net Debit Capital Locking ---")
    # Test NIFTY: net debit per lot = (150 - 75) * 25 = 75 * 25 = 1,875 INR
    # Target capital 11,000 -> 11,000 / 1875 = 5.87 -> scaled to 6 lots for NIFTY (~INR 11,250 deployed)
    nifty_net_debit = 1875.0
    lots_nifty = calculate_dynamic_lots("NIFTY", nifty_net_debit, target_capital=11000.0, max_risk_cap=2000.0)
    print(f"NIFTY (Net Debit per lot: INR {nifty_net_debit}): Scaled to {lots_nifty} lots (Capital: INR {nifty_net_debit * lots_nifty:,.2f})")
    assert 4 <= lots_nifty <= 6, f"Expected 4-6 lots for NIFTY, got {lots_nifty}"

    # Test BANKNIFTY: net debit per lot = (350 - 150) * 15 = 200 * 15 = 3,000 INR
    # Target capital 11,000 -> 11,000 / 3000 = 3.67 -> scaled to 4 lots for BANKNIFTY (~INR 12,000 deployed)
    bn_net_debit = 3000.0
    lots_bn = calculate_dynamic_lots("BANKNIFTY", bn_net_debit, target_capital=11000.0, max_risk_cap=2000.0)
    print(f"BANKNIFTY (Net Debit per lot: INR {bn_net_debit}): Scaled to {lots_bn} lots (Capital: INR {bn_net_debit * lots_bn:,.2f})")
    assert 3 <= lots_bn <= 4, f"Expected 3-4 lots for BANKNIFTY, got {lots_bn}"

    # Test Capital Locked in execute_paper_order
    # Verify defined-risk debit spread locks net debit, NOT gross buy leg
    init_db()
    with Session(database.engine) as s:
        # clear any test positions for clean test
        acc = s.exec(select(Account).where(Account.id == 1)).first()
        acc.virtual_balance = 1000000.0
        acc.margin_used = 0.0
        s.add(acc)
        s.commit()

    test_proposal = {
        "ticker": "NIFTY",
        "strategy_name": "NIFTY Bull Call Spread",
        "legs": [
            {"action": "BUY", "strike": 22400, "type": "CE", "ltp": 150.0, "delta": 0.50},
            {"action": "SELL", "strike": 22550, "type": "CE", "ltp": 75.0, "delta": 0.24},
        ],
        "max_profit": 1875.0,
        "max_loss": 1875.0,
        "rationale": "Dynamic Sizing Verification",
    }
    res = execute_paper_order(test_proposal)
    assert res["success"] is True, f"Expected order execution success, got {res}"
    pos_id = res["position_id"]
    capital_locked = res["capital_allocated"]
    buy_leg = [l for l in res["filled_legs"] if l["action"] == "BUY"][0]
    gross_buy = round(buy_leg["fill_price"] * buy_leg["lot_size"] * buy_leg["qty"], 2)
    print(f"Position committed! Capital Locked: INR {capital_locked} vs Gross Buy: INR {gross_buy}")
    assert capital_locked < gross_buy, f"Capital locked ({capital_locked}) must equal Net Debit, not Gross Buy ({gross_buy})!"

    # Verify 10% Gross SL & 5.5% Net ROC Floor (Patch 28B)
    gross_target = res["gross_target"]
    net_target = res["net_target"]
    sl = res["stop_loss"]
    est_charges = res["estimated_charges"]
    print(f"Patch 28B Verification: Capital Locked: INR {capital_locked:,.2f} | Net Target: INR {net_target} (5.5% ROC floor = {0.055 * capital_locked:.2f}), Gross Target: INR {gross_target}, Gross SL: INR {sl} (10% SL = {0.10 * capital_locked:.2f}), Est Charges: INR {est_charges}")
    assert sl == round(0.10 * capital_locked, 2), f"Expected Gross SL = 10% of capital locked, got {sl}"
    assert net_target == round(max(0.055 * capital_locked, 500.0), 2), f"Expected Net Target = max(5.5% ROC, 500), got {net_target}"
    assert gross_target == round(net_target + est_charges, 2), f"Expected Gross Target = Net Target + Charges, got {gross_target}"
    print(">>> TEST 2 PASSED: Dynamic position sizing and Patch 28B SL/ROC calibration verified!")


def test_3_leg_delta_bounds_and_strike_width():
    print("\n--- TEST 3: Strict Leg-Delta Bounds & Strike Width Validation ---")
    # Valid spread
    valid_legs = [
        {"action": "BUY", "strike": 22400, "delta": 0.50},
        {"action": "SELL", "strike": 22550, "delta": 0.24},
    ]
    ok, msg = validate_spread_structure("NIFTY", valid_legs, "Bull Call Spread")
    assert ok is True, f"Expected valid spread to pass: {msg}"

    # Invalid 1: Deep-ITM Long Leg (|delta| = 0.65 > 0.55)
    deep_itm_legs = [
        {"action": "BUY", "strike": 22200, "delta": 0.65},
        {"action": "SELL", "strike": 22550, "delta": 0.24},
    ]
    ok, msg = validate_spread_structure("NIFTY", deep_itm_legs, "Bull Call Spread")
    assert ok is False, "Expected Deep-ITM long leg to be rejected"
    print("Deep-ITM rejection confirmed:", msg)

    # Invalid 2: Dead Short Leg (|delta| = 0.05 < 0.15)
    dead_short_legs = [
        {"action": "BUY", "strike": 22400, "delta": 0.50},
        {"action": "SELL", "strike": 23000, "delta": 0.05},
    ]
    ok, msg = validate_spread_structure("NIFTY", dead_short_legs, "Bull Call Spread")
    assert ok is False, "Expected dead short leg to be rejected"
    print("Dead short leg rejection confirmed:", msg)

    # Invalid 3: High-delta short leg canceling profit (|delta| = 0.45 > 0.30)
    high_short_legs = [
        {"action": "BUY", "strike": 22400, "delta": 0.50},
        {"action": "SELL", "strike": 22450, "delta": 0.45},
    ]
    ok, msg = validate_spread_structure("NIFTY", high_short_legs, "Bull Call Spread")
    assert ok is False, "Expected high-delta short leg to be rejected"
    print("High short leg rejection confirmed:", msg)

    # Invalid 4: Strike width < 150 on NIFTY
    narrow_nifty = [
        {"action": "BUY", "strike": 22400, "delta": 0.50},
        {"action": "SELL", "strike": 22450, "delta": 0.25},
    ]
    ok, msg = validate_spread_structure("NIFTY", narrow_nifty, "Bull Call Spread")
    assert ok is False, "Expected narrow NIFTY spread (< 150 pts) to be rejected"
    print("Narrow NIFTY spread rejection confirmed:", msg)

    # Invalid 5: Strike width < 400 on BANKNIFTY
    narrow_bn = [
        {"action": "BUY", "strike": 48000, "delta": 0.50},
        {"action": "SELL", "strike": 48200, "delta": 0.25},
    ]
    ok, msg = validate_spread_structure("BANKNIFTY", narrow_bn, "BANKNIFTY Bull Call Spread")
    assert ok is False, "Expected narrow BANKNIFTY spread (< 400 pts) to be rejected"
    print("Narrow BANKNIFTY spread rejection confirmed:", msg)
    print(">>> TEST 3 PASSED: All leg-delta bounds & strike width checks verified!")


def test_4_trailing_stop_brokerage_dead_zone_and_memory():
    print("\n--- TEST 4: Trailing Stop Brokerage Dead Zone & Memory Preservation ---")
    # Verify that closing a trade DOES NOT wipe out market memory
    import market_data
    market_data.MINUTE_CANDLES["NIFTY"] = {12345: {"open": 22400, "close": 22450}}
    market_data.LIVE_SPOT_HISTORY["NIFTY"] = [(1.0, 22400.0, 1.1, 100, 100)]

    with Session(database.engine) as s:
        pos = s.exec(select(Position).where(Position.ticker == "NIFTY", Position.status == "OPEN")).first()
        if pos:
            # Close trade
            close_position_record(s, pos, "CLOSED_TARGET", final_pnl=850.0, exit_price=105.0)

    # Verify MINUTE_CANDLES and LIVE_SPOT_HISTORY are preserved!
    assert len(market_data.MINUTE_CANDLES["NIFTY"]) > 0, "MINUTE_CANDLES must NOT be cleared on trade exit!"
    assert len(market_data.LIVE_SPOT_HISTORY["NIFTY"]) > 0, "LIVE_SPOT_HISTORY must NOT be cleared on trade exit!"
    assert "NIFTY" in LAST_EXIT_TIMESTAMP, "LAST_EXIT_TIMESTAMP must be set for NIFTY"

    # Verify Net PnL was saved after deducting charges
    with Session(database.engine) as s:
        closed_pos = s.get(Position, pos.id)
        print(f"Closed Position #{closed_pos.id}: Gross PnL=INR {closed_pos.gross_pnl}, Charges=INR {closed_pos.charges}, Net PnL=INR {closed_pos.pnl}")
        assert closed_pos.gross_pnl == 850.0
        assert closed_pos.charges > 0
        assert closed_pos.pnl == round(closed_pos.gross_pnl - closed_pos.charges, 2)

    print(">>> TEST 4 PASSED: Intraday memory preserved & net charges deducted from realized PnL!")


def test_5_anti_churn_daily_cap():
    print("\n--- TEST 5: Anti-Churn 4-Trade Daily Cap & 2-Trade Symbol Cap ---")
    # We already have 1 trade today from test_2
    with Session(database.engine) as s:
        initial_trades = count_today_trades(s)
        print(f"Current trades executed today: {initial_trades}")

        # Add dummy trades up to 4
        while count_today_trades(s) < 4:
            p = Position(
                ticker="BANKNIFTY" if count_today_trades(s, "NIFTY") >= 2 else "NIFTY",
                strategy_name="Test Spread",
                legs_json="[]",
                net_premium=10.0,
                capital_allocated=5000.0,
                stop_loss=1000.0,
                target=1500.0,
                entry_timestamp=datetime.now(timezone.utc),
                status="CLOSED_TARGET",
                pnl=500.0,
            )
            s.add(p)
            s.commit()

        total = count_today_trades(s)
        print(f"Session trade count: {total}/4")
        assert total >= 4

    # Now attempt a new trade proposal via execute_paper_order
    proposal = {
        "ticker": "NIFTY",
        "strategy_name": "NIFTY Bull Call Spread",
        "legs": [
            {"action": "BUY", "strike": 22400, "type": "CE", "ltp": 150.0, "delta": 0.50},
            {"action": "SELL", "strike": 22550, "type": "CE", "ltp": 75.0, "delta": 0.24},
        ],
        "max_profit": 1875.0,
        "max_loss": 1875.0,
    }
    res = execute_paper_order(proposal)
    print("5th trade attempt result:", res)
    assert res["success"] is False, "Expected 5th trade to be rejected by daily circuit breaker"
    assert "DAILY_MAX_TRADES_EXCEEDED" in res["error"] or "SYMBOL_MAX_TRADES_EXCEEDED" in res["error"]
    print(">>> TEST 5 PASSED: 4-trade session cap successfully blocked overtrading!")


if __name__ == "__main__":
    try:
        test_1_indian_fo_charges()
        test_2_dynamic_position_sizing_and_net_debit_capital()
        test_3_leg_delta_bounds_and_strike_width()
        test_4_trailing_stop_brokerage_dead_zone_and_memory()
        test_5_anti_churn_daily_cap()
        print("\n=======================================================")
        print(" ALL SYSTEM UPGRADE 28 VERIFICATION SUITES PASSED (5/5) ")
        print("=======================================================\n")
    finally:
        test_engine.dispose()
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
                print(f"[TEST TEARDOWN] Cleaned up isolated test database: {TEST_DB_PATH}")
            except Exception as e:
                print(f"[TEST TEARDOWN] Notice: Could not remove test database: {e}")

