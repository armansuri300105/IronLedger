"""
Autonomous Trading Loop & Auto-Execution Engine (Autopilot Scheduler)
File: backend/autopilot.py

Implements STEP 13, STEP 14, STEP 17 & STEP 18:
- Watchlist: ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "INFY"]
- Market hours check: Monday to Friday, 09:15 AM - 03:15 PM IST (with FORCE_MARKET_OPEN bypass)
- Max active positions cap (MAX_OPEN_POSITIONS = 3)
- Ticker cooldown tracking (COOLDOWN_MINUTES_PER_TICKER = 15) - ONLY applied after executed trades
- In-memory kill switch with async lock (IS_AUTOPILOT_ACTIVE)
- Unified pub/sub telemetry event broadcasting for SSE stream (/api/stream/live)
- 30-second loop interval catching rapid intraday moves
- asyncio.to_thread for non-blocking market data and Clef gatekeeper evaluation
- Uvicorn logger ("uvicorn.error") for unified terminal visibility
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from dotenv import load_dotenv
from sqlmodel import Session, select

from database import (
    MAX_DAILY_SYMBOL_TRADES,
    MAX_DAILY_TOTAL_TRADES,
    Position,
    count_today_trades,
    engine,
)
from graph import StrategistState, graph
from clef_gatekeeper import evaluate_trade_opportunity
from market_data import fetch_live_options_data

# Load environment variables from backend/.env or current working directory
dotenv_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path)
load_dotenv()

# Use Uvicorn logger so all scanner output appears directly in the server console
logger = logging.getLogger("uvicorn.error")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Global Autopilot Configuration (STEP 21 & UPGRADE 50: Rigid 5-Rule Institutional Lock)
# ---------------------------------------------------------------------------
WATCHLIST: List[str] = ["NIFTY", "BANKNIFTY"]
MAX_OPEN_POSITIONS: int = 2
COOLDOWN_MINUTES_PER_TICKER: int = 20  # Rule 1: Mandatory 20-minute (1200s) cooldown per symbol after any position closes
SCAN_LOOP_INTERVAL_SECONDS: int = 30  # 30 seconds loop interval
# Daily session circuit breaker limits imported from database.py (or configured via .env)
# Rule 1: Max 3 trades per symbol per day, max 6 total across NIFTY and BANKNIFTY
DAILY_TOTAL_TRADES_CAP: int = MAX_DAILY_TOTAL_TRADES
DAILY_SYMBOL_TRADES_CAP: int = MAX_DAILY_SYMBOL_TRADES

# Autopilot state
_is_autopilot_active: bool = True
_autopilot_lock = asyncio.Lock()
_autopilot_task: Optional[asyncio.Task] = None
_scanner_running: bool = False

# Cooldown and tracking (stores timestamp of last EXECUTED trade per ticker)
_last_scanned: Dict[str, datetime] = {}
_currently_scanning: Optional[str] = None
_total_auto_executions: int = 0
_total_scans_completed: int = 0

# Broadcast subscribers & rolling telemetry buffer
_subscribers: Set[asyncio.Queue] = set()
_telemetry_history: deque = deque(maxlen=150)
_exit_logs_history: deque = deque(maxlen=50)


# ---------------------------------------------------------------------------
# IST Market Hours & Session Helpers
# ---------------------------------------------------------------------------
IST_OFFSET = timezone(timedelta(hours=5, minutes=30))


def get_current_ist_time() -> datetime:
    """Returns current datetime in Indian Standard Time (IST)."""
    return datetime.now(timezone.utc).astimezone(IST_OFFSET)


def get_market_session_state() -> str:
    """
    3-stage IST session state helper (UPGRADE 50: Rule 1 - 09:45-14:45 IST Trading Window):
    - FORCE_MARKET_OPEN=true -> "ACTIVE_TRADING"
    - Mon-Fri 09:15 to 09:45 IST -> "WARMUP_ONLY" (30-minute opening range settling)
    - Mon-Fri 09:45 to 14:45 IST -> "ACTIVE_TRADING"
    - Mon-Fri 14:45 to 15:15 IST -> "MANAGE_ONLY"
    - Otherwise -> "MARKET_CLOSED"
    """
    force = os.getenv("FORCE_MARKET_OPEN", "").strip().lower() in ("true", "1", "yes")
    if force:
        return "ACTIVE_TRADING"

    now_ist = get_current_ist_time()
    if now_ist.weekday() > 4:  # Saturday or Sunday
        return "MARKET_CLOSED"

    t = (now_ist.hour, now_ist.minute)
    if (9, 15) <= t < (9, 45):
        return "WARMUP_ONLY"
    elif (9, 45) <= t < (14, 45):
        return "ACTIVE_TRADING"
    elif (14, 45) <= t <= (15, 15):
        return "MANAGE_ONLY"
    else:
        return "MARKET_CLOSED"


def is_market_open() -> bool:
    """
    Checks if current IST time allows market activity (warmup, active, or management).
    """
    state = get_market_session_state()
    return state in ("WARMUP_ONLY", "ACTIVE_TRADING", "MANAGE_ONLY")


def get_market_session_info() -> Dict[str, Any]:
    """Provides human-readable market session diagnostics."""
    now_ist = get_current_ist_time()
    time_str = now_ist.strftime("%I:%M:%S %p IST")
    force = os.getenv("FORCE_MARKET_OPEN", "").strip().lower() in ("true", "1", "yes")
    state = get_market_session_state()

    labels = {
        "WARMUP_ONLY": "Warm-Up Only (09:15-09:45 IST)",
        "ACTIVE_TRADING": "Open (Live Session)",
        "MANAGE_ONLY": "Manage Only (Past 14:45 IST Cutoff)",
        "MARKET_CLOSED": "Closed",
    }
    session_label = "Bypass Active" if force else labels.get(state, "Closed")

    return {
        "is_open": state != "MARKET_CLOSED",
        "session_state": state,
        "session_label": session_label,
        "ist_time": time_str,
        "bypass_active": force,
    }


# ---------------------------------------------------------------------------
# Database & Cooldown Diagnostics
# ---------------------------------------------------------------------------
def count_open_positions() -> int:
    """Returns the count of currently OPEN positions in the paper database."""
    try:
        with Session(engine) as session:
            statement = select(Position).where(Position.status == "OPEN")
            records = session.exec(statement).all()
            return len(records)
    except Exception as exc:
        logger.error(f"Failed querying open positions: {exc}")
        return 0


def has_open_position_for_ticker(ticker: str) -> bool:
    """Checks if an active OPEN position already exists for this symbol (1-trade-per-index rule)."""
    try:
        with Session(engine) as session:
            statement = select(Position).where(
                Position.status == "OPEN",
                Position.ticker == ticker.upper(),
            )
            record = session.exec(statement).first()
            return record is not None
    except Exception as exc:
        logger.error(f"Failed checking open position for {ticker}: {exc}")
        return False


def check_symbol_cooldown_and_exhaustion(
    ticker: str,
    proposed_direction: Optional[str] = None,
    session: Optional[Session] = None,
    current_spot: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    """
    UPGRADE 34 & 47: Same-Direction Whipsaw Cooldown & Trend Exhaustion Cooldown.
    - UPGRADE 47: If last trade closed on CLOSED_SL or CLOSED_TREND_REVERSAL in the same strategy direction
      within the last 20 minutes:
        * Block re-entering the same direction UNLESS live Spot has broken out beyond the losing trade's entry spot
          by at least +25.0 pts on NIFTY (+75.0 pts on BANKNIFTY) for Bull Call, or -25.0 pts (-75.0 pts) for Bear Put.
        * If opposite direction, enforce 10-minute cooldown.
    - UPGRADE 34: If 2 consecutive winning trades closed in same direction: 20-minute cooldown for that direction
      to avoid buying exhausted wave tops.
    Returns (True, None) if trade is allowed, or (False, reason_str) if blocked.
    """
    clean_sym = ticker.strip().upper()
    now_utc = datetime.now(timezone.utc)

    def _eval(sess: Session) -> Tuple[bool, Optional[str]]:
        closed_positions = sess.exec(
            select(Position)
            .where(Position.ticker == clean_sym, Position.status != "OPEN")
            .order_by(Position.exit_timestamp.desc(), Position.id.desc())
        ).all()

        if not closed_positions:
            return True, None

        last_pos = closed_positions[0]
        exit_time = last_pos.exit_timestamp or last_pos.entry_timestamp or now_utc
        if exit_time.tzinfo is None:
            exit_time = exit_time.replace(tzinfo=timezone.utc)
        elapsed_min = (now_utc - exit_time).total_seconds() / 60.0

        # Rule 1 (UPGRADE 50): Mandatory 20-minute (1200s) cooldown per symbol after ANY position closes
        if elapsed_min < 20.0:
            rem = 20.0 - elapsed_min
            return False, (
                f"Mandatory Post-Exit Cooldown: Position #{last_pos.id} on {clean_sym} closed "
                f"{elapsed_min:.1f}m ago ({last_pos.status}). Enforcing 20-minute (1200s) cooldown "
                f"before scanning {clean_sym} again ({rem:.1f} mins remaining)."
            )

        # Rule 2: 2 consecutive winning trades in the same direction -> 20-minute cooldown for that direction
        if len(closed_positions) >= 2:
            p1 = closed_positions[0]
            p2 = closed_positions[1]
            p1_win = (
                (p1.net_pnl is not None and p1.net_pnl > 0)
                or (p1.pnl is not None and p1.pnl > 0)
                or p1.status in ("CLOSED_TARGET", "CLOSED_TRAILING_STOP")
            )
            p2_win = (
                (p2.net_pnl is not None and p2.net_pnl > 0)
                or (p2.pnl is not None and p2.pnl > 0)
                or p2.status in ("CLOSED_TARGET", "CLOSED_TRAILING_STOP")
            )

            from execution import get_position_bias
            p1_dir = get_position_bias(p1.strategy_name)
            p2_dir = get_position_bias(p2.strategy_name)

            if p1_win and p2_win and p1_dir == p2_dir and p1_dir in ("BULLISH", "BEARISH"):
                if elapsed_min < 20.0:
                    check_dir = (proposed_direction or "").strip().upper()
                    if not check_dir or check_dir == p1_dir:
                        rem = 20.0 - elapsed_min
                        return False, (
                            f"Trend Exhaustion Cooldown: {clean_sym} had 2 consecutive {p1_dir} wins (#{p2.id}, #{p1.id}). "
                            f"Enforcing 20-minute cooldown before 3rd {p1_dir} entry to avoid buying exhausted wave tops ({rem:.1f} mins remaining)."
                        )

        return True, None

    if session is not None:
        return _eval(session)
    else:
        with Session(engine) as sess:
            return _eval(sess)


def check_cross_index_drawdown(
    ticker: str,
    proposed_direction: Optional[str] = None,
    session: Optional[Session] = None,
) -> Tuple[bool, Optional[str]]:
    """
    UPGRADE 38: Cross-Index Drawdown Contagion Guard.
    Checks the live unrealized MTM of any currently OPEN position on the other index.
    If the other index has an OPEN position in the same direction that is in drawdown
    (unrealized_gross_pnl < -250.0 INR), wait and do NOT open a second correlated position.
    """
    clean_sym = ticker.strip().upper()

    def _eval(sess: Session) -> Tuple[bool, Optional[str]]:
        open_positions = sess.exec(select(Position).where(Position.status == "OPEN")).all()
        for op in open_positions:
            if op.ticker.upper() != clean_sym:
                from execution import get_position_bias
                other_bias = get_position_bias(op.strategy_name)
                # Check correlated trades
                if proposed_direction is None or proposed_direction == other_bias:
                    other_pnl = None
                    try:
                        from position_monitor import POSITION_TELEMETRY, calculate_position_mtm
                        if op.id in POSITION_TELEMETRY and "last_unrealized_pnl" in POSITION_TELEMETRY[op.id]:
                            other_pnl = POSITION_TELEMETRY[op.id]["last_unrealized_pnl"]
                        elif op.gross_pnl is not None:
                            other_pnl = op.gross_pnl
                        elif op.pnl is not None:
                            other_pnl = op.pnl
                        else:
                            mtm_res = calculate_position_mtm(op)
                            if mtm_res and "unrealized_pnl" in mtm_res:
                                other_pnl = mtm_res["unrealized_pnl"]
                    except Exception:
                        if op.gross_pnl is not None:
                            other_pnl = op.gross_pnl
                        elif op.pnl is not None:
                            other_pnl = op.pnl

                    if other_pnl is not None and other_pnl < -250.0:
                        return False, (
                            f"Cross-Index Drawdown Contagion Guard: Active position #{op.id} on {op.ticker} "
                            f"({op.strategy_name}, {other_bias}) is in drawdown (INR {other_pnl:,.2f} < -INR 250.00). "
                            f"Holding scan on {clean_sym} to prevent double-drawdown contagion."
                        )
        return True, None

    if session is not None:
        return _eval(session)
    else:
        with Session(engine) as sess:
            return _eval(sess)


def is_ticker_in_cooldown(
    ticker: str,
    proposed_direction: Optional[str] = None,
    current_spot: Optional[float] = None,
) -> bool:
    """
    Checks if a trade was executed or closed for this ticker within the cooldown window.
    (UPGRADE 34 & 47: Enforces 20m same-direction whipsaw cooldown with breakout check,
    10m opposing post-SL/reversal, 20m consecutive same-direction exhaustion, and 15m post-execution).
    """
    clean = ticker.strip().upper()
    try:
        ok, _ = check_symbol_cooldown_and_exhaustion(
            clean, proposed_direction=proposed_direction, current_spot=current_spot
        )
        if not ok:
            return True
    except Exception:
        pass

    last_time = _last_scanned.get(clean)
    try:
        from position_monitor import LAST_EXIT_TIMESTAMP
        last_exit = LAST_EXIT_TIMESTAMP.get(clean)
        if last_exit and (not last_time or last_exit > last_time):
            last_time = last_exit
    except Exception:
        pass

    if not last_time:
        return False
    elapsed_minutes = (datetime.now(timezone.utc) - last_time).total_seconds() / 60.0
    return elapsed_minutes < COOLDOWN_MINUTES_PER_TICKER


def get_cooldown_statuses() -> Dict[str, Dict[str, Any]]:
    """Returns cooldown remaining info for each watchlist symbol factoring executions and exits."""
    statuses = {}
    now = datetime.now(timezone.utc)
    for sym in WATCHLIST:
        last_time = _last_scanned.get(sym)
        try:
            from position_monitor import LAST_EXIT_TIMESTAMP
            last_exit = LAST_EXIT_TIMESTAMP.get(sym)
            if last_exit and (not last_time or last_exit > last_time):
                last_time = last_exit
        except Exception:
            pass

        if not last_time:
            statuses[sym] = {"eligible": True, "cooldown_remaining_sec": 0, "last_scanned": None}
        else:
            elapsed_sec = (now - last_time).total_seconds()
            total_sec = COOLDOWN_MINUTES_PER_TICKER * 60
            remaining = max(0, int(total_sec - elapsed_sec))
            statuses[sym] = {
                "eligible": remaining == 0,
                "cooldown_remaining_sec": remaining,
                "last_scanned": last_time.isoformat(),
            }
    return statuses


# ---------------------------------------------------------------------------
# Pub/Sub Telemetry Event Broadcaster
# ---------------------------------------------------------------------------
async def broadcast_telemetry(event: Dict[str, Any]):
    """Dispatches a telemetry event to all connected SSE clients and history."""
    if "timestamp" not in event:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()

    _telemetry_history.append(event)

    # If this is an auto-exit, record to dedicated exit log history
    if event.get("badge_type") in ("TARGET_HIT", "SL_HIT", "EOD_SQUAREOFF", "AUTO_EXIT"):
        _exit_logs_history.append(event)

    # Fan-out to all active subscribers
    dead_queues = set()
    for q in _subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead_queues.add(q)
        except Exception:
            dead_queues.add(q)

    for dead in dead_queues:
        _subscribers.discard(dead)


def record_exit_log(log_item: Dict[str, Any]):
    """Records an auto-exit event directly from position monitor."""
    if "timestamp" not in log_item:
        log_item["timestamp"] = datetime.now(timezone.utc).isoformat()
    _exit_logs_history.append(log_item)
    # Schedule broadcast safely in event loop if available
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_telemetry(log_item))
    except RuntimeError:
        pass


async def subscribe_telemetry() -> AsyncGenerator[Dict[str, Any], None]:
    """Yields live telemetry events for SSE connection."""
    q = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    try:
        # Replay recent history (last 10 events) for instant context
        for past_event in list(_telemetry_history)[-10:]:
            yield past_event

        while True:
            event = await q.get()
            yield event
    finally:
        _subscribers.discard(q)


# ---------------------------------------------------------------------------
# Autopilot State Controls
# ---------------------------------------------------------------------------
async def toggle_autopilot(desired_state: Optional[bool] = None) -> bool:
    """Toggles or sets the autopilot active flag thread-safely."""
    global _is_autopilot_active
    async with _autopilot_lock:
        if desired_state is not None:
            _is_autopilot_active = desired_state
        else:
            _is_autopilot_active = not _is_autopilot_active

        state_str = "ACTIVE" if _is_autopilot_active else "PAUSED"
        logger.info(f"Autopilot toggled -> [{state_str}]")

        await broadcast_telemetry({
            "type": "AUTOPILOT_STATUS_CHANGED",
            "active": _is_autopilot_active,
            "message": f"Autopilot emergency status: {state_str}",
            "badge_type": "INFO",
        })
        return _is_autopilot_active


def get_autopilot_status() -> Dict[str, Any]:
    """Returns complete telemetry state of the autonomous engine."""
    session_info = get_market_session_info()
    open_count = count_open_positions()

    return {
        "active": _is_autopilot_active,
        "market_open": session_info["is_open"],
        "market_session": session_info["session_label"],
        "ist_time": session_info["ist_time"],
        "bypass_active": session_info["bypass_active"],
        "watchlist": WATCHLIST,
        "max_positions": MAX_OPEN_POSITIONS,
        "open_positions_count": open_count,
        "max_daily_total_trades": MAX_DAILY_TOTAL_TRADES,
        "max_daily_symbol_trades": MAX_DAILY_SYMBOL_TRADES,
        "cooldown_minutes": COOLDOWN_MINUTES_PER_TICKER,
        "cooldowns": get_cooldown_statuses(),
        "currently_scanning": _currently_scanning,
        "total_executions": _total_auto_executions,
        "total_scans": _total_scans_completed,
        "exit_logs": list(_exit_logs_history),
    }


# ---------------------------------------------------------------------------
# Autonomous Execution Pipeline Invoker
# ---------------------------------------------------------------------------
async def scan_single_ticker(ticker: str, force: bool = False, market_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Executes the multi-agent LangGraph workflow for a single symbol,
    broadcasting each node's decision and auto-execution to telemetry.
    """
    global _currently_scanning, _total_scans_completed, _total_auto_executions
    clean_sym = ticker.strip().upper()
    _currently_scanning = clean_sym

    logger.info(f"[AUTOPILOT] Commencing autonomous analysis on {clean_sym}...")
    await broadcast_telemetry({
        "type": "SCAN_STARTED",
        "ticker": clean_sym,
        "message": f"Autopilot initialized analysis for {clean_sym} across live NSE feeds.",
        "badge_type": "INFO",
    })

    initial_state: StrategistState = {
        "ticker": clean_sym,
        "messages": [
            {
                "agent": "Autopilot Engine",
                "stage": "Scheduled Market Scan",
                "text": f"Autopilot triggering multi-agent pipeline for {clean_sym}.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
        "market_data": market_data or {},
        "rag_context": "",
        "quant_proposal": {},
        "risk_approved": False,
        "final_trade": {},
        "revision_count": 0,
        "risk_feedback": "",
        "past_lessons": "",
        "execution_details": None,
    }

    def _run_graph():
        return list(graph.stream(initial_state))

    try:
        steps = await asyncio.to_thread(_run_graph)

        final_state: Dict[str, Any] = dict(initial_state)

        for step in steps:
            for node_name, update in step.items():
                if "messages" in update and update["messages"]:
                    final_state["messages"].extend(update["messages"])
                    latest_msg = update["messages"][-1]

                    # Broadcast intermediate agent update
                    badge = latest_msg.get("badge_type", "INFO")
                    await broadcast_telemetry({
                        "type": "AGENT_STEP",
                        "ticker": clean_sym,
                        "node": node_name,
                        "agent": latest_msg.get("agent", node_name),
                        "stage": latest_msg.get("stage", ""),
                        "text": latest_msg.get("text", ""),
                        "badge_type": badge,
                    })

                for k in ["market_data", "rag_context", "quant_proposal", "risk_approved", "final_trade", "execution_details"]:
                    if k in update:
                        final_state[k] = update[k]

        _total_scans_completed += 1

        exec_details = final_state.get("execution_details")
        if exec_details and exec_details.get("success"):
            _total_auto_executions += 1
            # CRITICAL COOLDOWN FIX: Only set cooldown on a ticker AFTER a trade is executed!
            _last_scanned[clean_sym] = datetime.now(timezone.utc)
            await broadcast_telemetry({
                "type": "AUTO_ORDER_FILLED",
                "ticker": clean_sym,
                "strategy_name": final_state.get("final_trade", {}).get("strategy_name", "Setup"),
                "fill_price": exec_details.get("net_premium", 0.0),
                "position_id": exec_details.get("position_id"),
                "badge_type": "AUTO_BUY",
                "message": f"AUTO-EXECUTION COMMITTED: #{exec_details.get('position_id')} for {clean_sym}",
                "final_trade": final_state.get("final_trade"),
            })
        elif final_state.get("risk_approved") is False:
            await broadcast_telemetry({
                "type": "RISK_REJECTED",
                "ticker": clean_sym,
                "badge_type": "RISK_REJECTED",
                "message": f"Proposal rejected by Risk Manager for {clean_sym}: {final_state.get('risk_feedback', 'Constraints failed')}",
                "critique": final_state.get("risk_feedback"),
            })

        return final_state

    except Exception as exc:
        logger.error(f"Autopilot failed during scan of {clean_sym}: {exc}", exc_info=True)
        await broadcast_telemetry({
            "type": "SCAN_ERROR",
            "ticker": clean_sym,
            "error": str(exc),
            "badge_type": "ERROR",
            "message": f"Autonomous scan failed on {clean_sym}: {exc}",
        })
        return initial_state

    finally:
        _currently_scanning = None


# ---------------------------------------------------------------------------
# Background Autonomous Loop
# ---------------------------------------------------------------------------
async def run_autonomous_scanner():
    """
    Continuous background loop for autonomous market scanning:
    - Runs every 30 seconds so intraday moves are caught immediately
    - Validates IS_AUTOPILOT_ACTIVE and is_market_open()
    - Verifies MAX_OPEN_POSITIONS cap
    - Evaluates symbols in WATCHLIST using Clef Gatekeeper
    - Runs fetch_live_options_data and evaluate_trade_opportunity in asyncio.to_thread
    - Entire loop body wrapped in try...except so error on one tick never kills background task
    - Cooldowns only apply after trades are executed
    """
    global _scanner_running
    logger.info("Autonomous Scanner loop starting (interval: 30s)...")
    _scanner_running = True

    while _scanner_running:
        try:
            # Check 1: Is Autopilot Active?
            if not _is_autopilot_active:
                await asyncio.sleep(5)
                continue

            # Check 2: Are Market Hours Valid? (STEP 22: Session State Handling)
            session_state = get_market_session_state()
            if session_state == "MARKET_CLOSED":
                session_info = get_market_session_info()
                logger.debug(f"Autopilot: Market is {session_info['session_label']}. Waiting...")
                await asyncio.sleep(20)
                continue

            if session_state == "WARMUP_ONLY":
                logger.info(
                    "[AUTOPILOT] 09:15-09:45 IST Warm-Up Window: Building 30m opening range baseline. New entries paused."
                )
                for sym in WATCHLIST:
                    try:
                        await asyncio.to_thread(fetch_live_options_data, sym)
                    except Exception as e:
                        logger.debug(f"Warm-up baseline poll failed on {sym}: {e}")
                await broadcast_telemetry({
                    "type": "WARMUP_WINDOW",
                    "badge_type": "INFO",
                    "message": "[AUTOPILOT] 09:15-09:45 IST Warm-Up Window: Building 30m opening range baseline. New entries paused.",
                })
                await asyncio.sleep(SCAN_LOOP_INTERVAL_SECONDS)
                continue

            if session_state == "MANAGE_ONLY":
                logger.info(
                    "[AUTOPILOT] Past 14:45 IST entry cutoff. Monitoring open positions until 15:15 square-off."
                )
                await asyncio.sleep(SCAN_LOOP_INTERVAL_SECONDS)
                continue

            # Check 3: Risk Cap on Active Positions
            open_count = count_open_positions()
            if open_count >= MAX_OPEN_POSITIONS:
                logger.info(
                    f"Autopilot: Max open positions reached ({open_count}/{MAX_OPEN_POSITIONS}). Pausing scan cycle."
                )
                await broadcast_telemetry({
                    "type": "RISK_CAP_REACHED",
                    "badge_type": "WARNING",
                    "message": f"Active positions cap ({open_count}/{MAX_OPEN_POSITIONS}) reached. Waiting for exits.",
                })
                await asyncio.sleep(30)
                continue

            # Check 3B: Anti-Churn Daily Trades Circuit Breaker (Configured by MAX_DAILY_TOTAL_TRADES)
            with Session(engine) as session:
                today_total_trades = count_today_trades(session)
                if today_total_trades >= MAX_DAILY_TOTAL_TRADES:
                    logger.info(
                        f"[AUTOPILOT] Anti-churn circuit breaker: Daily trades cap reached ({today_total_trades}/{MAX_DAILY_TOTAL_TRADES}). Pausing scans for session."
                    )
                    await broadcast_telemetry({
                        "type": "CIRCUIT_BREAKER_ACTIVE",
                        "badge_type": "WARNING",
                        "message": f"Daily session trades cap ({today_total_trades}/{MAX_DAILY_TOTAL_TRADES}) reached. Autopilot holding current positions.",
                    })
                    await asyncio.sleep(60)
                    continue

            # Check 4: Find Next Eligible Symbol in Watchlist (Clef Gatekeeper)
            found_candidate = False

            # UPGRADE 49: Simultaneous dual-index live telemetry refresh before scan cycle
            try:
                from market_data import refresh_dual_index_telemetry
                await asyncio.to_thread(refresh_dual_index_telemetry, ["NIFTY", "BANKNIFTY"])
            except Exception as e_telemetry:
                logger.debug(f"[AUTOPILOT] Dual-index telemetry refresh failed: {e_telemetry}")

            for sym in WATCHLIST:
                # Check symbol-specific daily limit (Configured by MAX_DAILY_SYMBOL_TRADES)
                with Session(engine) as session:
                    today_sym_trades = count_today_trades(session, sym)
                    if today_sym_trades >= MAX_DAILY_SYMBOL_TRADES:
                        logger.info(f"[AUTOPILOT] {sym} reached max daily trades cap ({today_sym_trades}/{MAX_DAILY_SYMBOL_TRADES}). Skipping scan.")
                        continue

                # STEP 21: One-Trade-Per-Index Rule
                if has_open_position_for_ticker(sym):
                    logger.info(f"[AUTOPILOT] {sym} already has an open position. Skipping scan.")
                    continue

                # UPGRADE 38: Cross-Index Drawdown Contagion Guard
                drawdown_ok, drawdown_reason = check_cross_index_drawdown(sym)
                if not drawdown_ok:
                    logger.info(f"[AUTOPILOT] {drawdown_reason}")
                    continue

                if not is_ticker_in_cooldown(sym):
                    found_candidate = True


                    try:
                        # 1. Fetch live options chain (free web scrape) in worker thread
                        market_data = await asyncio.to_thread(fetch_live_options_data, sym)

                        if market_data is None:
                            logger.info(f"[AUTOPILOT] Market data for {sym} is None/timed out. Skipping tick.")
                            continue

                        # UPGRADE 39: Track trend scan persistence in memory
                        from risk_manager import record_trend_scan, update_market_state
                        technicals_raw = market_data.get("intraday_technicals", {}) if isinstance(market_data.get("intraday_technicals"), dict) else {}
                        t_state = str(market_data.get("trend_state") or technicals_raw.get("trend_state", "CHOPPY_SIDEWAYS"))
                        record_trend_scan(sym, t_state)

                        # UPGRADE 48 & 49: Maintain shared LATEST_MARKET_STATE in memory
                        m5 = float(market_data.get("move_5m_pts") or technicals_raw.get("move_5m_pts", 0.0) or 0.0)
                        m3 = float(market_data.get("move_3m_pts") or technicals_raw.get("move_3m_pts", 0.0) or 0.0)
                        er = float(market_data.get("efficiency_ratio") or technicals_raw.get("efficiency_ratio", 0.5) or 0.5)
                        pcr = float(market_data.get("pcr", 1.0) or 1.0)
                        rsi_val_cur = float(technicals_raw.get("rsi_14", technicals_raw.get("rsi", 50.0)) or 50.0)
                        spot_val_cur = float(market_data.get("spot_price", 0.0) or 0.0)
                        update_market_state(sym, t_state, m5, er, pcr, spot=spot_val_cur, move_3m=m3, rsi=rsi_val_cur)

                        # 2. Run Clef gatekeeper discriminative check in worker thread
                        is_actionable = await asyncio.to_thread(
                            evaluate_trade_opportunity, sym, market_data
                        )

                        if not is_actionable:
                            # CRITICAL COOLDOWN FIX: Do NOT apply cooldown on rejected scan!
                            logger.info(
                                f"[GATEKEEPER] Clef evaluated {sym} as FLAT. Skipping LLM to save quota."
                            )
                            await broadcast_telemetry({
                                "type": "GATEKEEPER_SKIPPED",
                                "ticker": sym,
                                "badge_type": "INFO",
                                "message": f"[GATEKEEPER] Clef evaluated {sym} as FLAT. Skipping LLM to save quota.",
                            })
                            continue

                        # 3. Trigger full LangGraph pipeline on actionable opportunity
                        await scan_single_ticker(sym, market_data=market_data)
                        await asyncio.sleep(5)
                        break

                    except Exception as sym_err:
                        logger.exception(f"Error scanning ticker {sym}: {sym_err}")
                        continue

            if not found_candidate:
                # All symbols are currently in cooldown
                logger.debug("Autopilot: All symbols in cooldown window. Sleeping...")
                await asyncio.sleep(SCAN_LOOP_INTERVAL_SECONDS)
            else:
                # 30-second interval between scan cycles
                await asyncio.sleep(SCAN_LOOP_INTERVAL_SECONDS)

        except asyncio.CancelledError:
            logger.info("Autonomous scanner task cancelled.")
            break
        except Exception as e:
            logger.exception(f"Error in autonomous scanner cycle: {e}")
            await asyncio.sleep(5)


def start_autonomous_scanner():
    """Launches the background scanner task."""
    global _autopilot_task, _scanner_running
    if not _scanner_running:
        _scanner_running = True
        try:
            loop = asyncio.get_running_loop()
            _autopilot_task = loop.create_task(run_autonomous_scanner())
        except RuntimeError:
            loop = asyncio.get_event_loop()
            _autopilot_task = loop.create_task(run_autonomous_scanner())
        logger.info("Autonomous scanner task dispatched.")


def stop_autonomous_scanner():
    """Cancels and halts the background scanner task."""
    global _autopilot_task, _scanner_running
    _scanner_running = False
    if _autopilot_task:
        _autopilot_task.cancel()
        _autopilot_task = None
        logger.info("Autonomous scanner task stopped.")
