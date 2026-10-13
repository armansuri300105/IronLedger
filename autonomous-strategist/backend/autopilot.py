"""
Autonomous Trading Loop & Auto-Execution Engine (Autopilot Scheduler)
File: backend/autopilot.py

Implements STEP 13 & STEP 14:
- Watchlist: ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "INFY"]
- Market hours check: Monday to Friday, 09:15 AM - 03:15 PM IST (with FORCE_MARKET_OPEN bypass)
- Max active positions cap (MAX_OPEN_POSITIONS = 3)
- Ticker cooldown tracking (COOLDOWN_MINUTES_PER_TICKER = 30)
- In-memory kill switch with async lock (IS_AUTOPILOT_ACTIVE)
- Unified pub/sub telemetry event broadcasting for SSE stream (/api/stream/live)
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from dotenv import load_dotenv
from sqlmodel import Session, select

from database import Position, engine
from graph import StrategistState, graph

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("autopilot_scheduler")

# ---------------------------------------------------------------------------
# Global Autopilot Configuration
# ---------------------------------------------------------------------------
WATCHLIST: List[str] = ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "INFY"]
MAX_OPEN_POSITIONS: int = 3
COOLDOWN_MINUTES_PER_TICKER: int = 30
SCAN_LOOP_INTERVAL_SECONDS: int = 15

# Autopilot state
_is_autopilot_active: bool = True
_autopilot_lock = asyncio.Lock()
_autopilot_task: Optional[asyncio.Task] = None
_scanner_running: bool = False

# Cooldown and tracking
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


def is_market_open() -> bool:
    """
    Checks if current IST time is within Indian market trading hours:
    Monday to Friday, 09:15 AM to 03:15 PM IST.
    Supports dev bypass: FORCE_MARKET_OPEN=true in .env
    """
    force = os.getenv("FORCE_MARKET_OPEN", "").strip().lower() in ("true", "1", "yes")
    if force:
        return True

    now_ist = get_current_ist_time()
    # 0 = Monday, 4 = Friday, 5 = Saturday, 6 = Sunday
    if now_ist.weekday() > 4:
        return False

    market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now_ist.replace(hour=15, minute=15, second=0, microsecond=0)
    return market_open <= now_ist <= market_close


def get_market_session_info() -> Dict[str, Any]:
    """Provides human-readable market session diagnostics."""
    now_ist = get_current_ist_time()
    time_str = now_ist.strftime("%I:%M:%S %p IST")
    force = os.getenv("FORCE_MARKET_OPEN", "").strip().lower() in ("true", "1", "yes")

    if force:
        session_label = "Bypass Active"
        is_open = True
    elif now_ist.weekday() > 4:
        session_label = "Closed (Weekend)"
        is_open = False
    else:
        market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now_ist.replace(hour=15, minute=15, second=0, microsecond=0)
        if now_ist < market_open:
            session_label = "Pre-Market (Opens 09:15 AM)"
            is_open = False
        elif now_ist > market_close:
            session_label = "Closed (Post-Market)"
            is_open = False
        else:
            session_label = "Open (Live Session)"
            is_open = True

    return {
        "is_open": is_open,
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


def is_ticker_in_cooldown(ticker: str) -> bool:
    """Checks if a ticker was scanned within the cooldown window."""
    last_time = _last_scanned.get(ticker.upper())
    if not last_time:
        return False
    elapsed_minutes = (datetime.now(timezone.utc) - last_time).total_seconds() / 60.0
    return elapsed_minutes < COOLDOWN_MINUTES_PER_TICKER


def get_cooldown_statuses() -> Dict[str, Dict[str, Any]]:
    """Returns cooldown remaining info for each watchlist symbol."""
    statuses = {}
    now = datetime.now(timezone.utc)
    for sym in WATCHLIST:
        last_time = _last_scanned.get(sym)
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
async def scan_single_ticker(ticker: str, force: bool = False) -> Dict[str, Any]:
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
        "market_data": {},
        "rag_context": "",
        "quant_proposal": {},
        "risk_approved": False,
        "final_trade": {},
        "revision_count": 0,
        "risk_feedback": "",
        "past_lessons": "",
        "execution_details": None,
    }

    loop = asyncio.get_event_loop()

    def _run_graph():
        return list(graph.stream(initial_state))

    try:
        steps = await loop.run_in_executor(None, _run_graph)

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

        # Record scan completion & update cooldown
        _last_scanned[clean_sym] = datetime.now(timezone.utc)
        _total_scans_completed += 1

        exec_details = final_state.get("execution_details")
        if exec_details and exec_details.get("success"):
            _total_auto_executions += 1
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
    - Runs every 15-30 seconds
    - Validates IS_AUTOPILOT_ACTIVE and is_market_open()
    - Verifies MAX_OPEN_POSITIONS cap
    - Scans eligible symbols in WATCHLIST
    """
    global _scanner_running
    logger.info("Autonomous Scanner loop starting...")
    _scanner_running = True

    while _scanner_running:
        try:
            # Check 1: Is Autopilot Active?
            if not _is_autopilot_active:
                await asyncio.sleep(5)
                continue

            # Check 2: Are Market Hours Valid?
            if not is_market_open():
                session_info = get_market_session_info()
                logger.debug(f"Autopilot: Market is {session_info['session_label']}. Waiting...")
                await asyncio.sleep(20)
                continue

            # Check 3: Risk Cap on Active Positions
            open_count = count_open_positions()
            if open_count >= MAX_OPEN_POSITIONS:
                logger.info(f"Autopilot: Max open positions reached ({open_count}/{MAX_OPEN_POSITIONS}). Pausing scan cycle.")
                await broadcast_telemetry({
                    "type": "RISK_CAP_REACHED",
                    "badge_type": "WARNING",
                    "message": f"Active positions cap ({open_count}/{MAX_OPEN_POSITIONS}) reached. Waiting for exits.",
                })
                await asyncio.sleep(30)
                continue

            # Check 4: Find Next Eligible Symbol in Watchlist
            found_candidate = False
            for sym in WATCHLIST:
                if not is_ticker_in_cooldown(sym):
                    found_candidate = True
                    await scan_single_ticker(sym)
                    # Brief inter-symbol delay to avoid rate limiting
                    await asyncio.sleep(5)
                    break

            if not found_candidate:
                # All symbols are currently in cooldown
                logger.debug("Autopilot: All symbols in cooldown window. Sleeping...")
                await asyncio.sleep(SCAN_LOOP_INTERVAL_SECONDS)

        except asyncio.CancelledError:
            logger.info("Autonomous scanner task cancelled.")
            break
        except Exception as exc:
            logger.error(f"Error in autonomous scanner cycle: {exc}", exc_info=True)
            await asyncio.sleep(10)


def start_autonomous_scanner():
    """Launches the background scanner task."""
    global _autopilot_task, _scanner_running
    if not _scanner_running:
        _scanner_running = True
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
