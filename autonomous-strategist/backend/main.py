"""
Autonomous Equity & Options Strategist - FastAPI Backend
File: backend/main.py

Implements STEP 3, STEP 9, STEP 10, STEP 11, STEP 13, and STEP 14:
- Real-time Server-Sent Events (SSE) streaming of multi-agent LangGraph execution
- Autonomous trading engine & scheduler: GET /api/autopilot/status, POST /api/autopilot/toggle
- Unified live telemetry SSE stream: GET /api/stream/live
- Paper trading ledger APIs: GET /api/portfolio, POST /api/trade/execute
- Position lifecycle APIs: GET /api/positions/history, POST /api/positions/{id}/close
- Learned memory API: GET /api/lessons/{ticker}
- Background position monitoring & autonomous scan loop via FastAPI lifespan
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from autopilot import (
    get_autopilot_status,
    scan_single_ticker,
    start_autonomous_scanner,
    stop_autonomous_scanner,
    subscribe_telemetry,
    toggle_autopilot,
)
from buggy_monitor import format_exit_reason, infer_strategy_bias, stream_buggy_signals
from database import Account, Position, engine, get_account, init_db
from execution import execute_paper_order, get_lot_size, LOT_SIZES
from graph import StrategistState, graph
from position_monitor import (
    calculate_position_mtm,
    close_position_by_id,
    start_position_monitor,
    stop_position_monitor,
)
from rag_engine import retrieve_relevant_lessons

# STEP 27: Persistent Session Logging to logs/session_YYYYMMDD.log
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs("logs", exist_ok=True)

session_date = datetime.now().strftime("%Y%m%d")
session_log_name = f"session_{session_date}.log"
session_log_path = os.path.join(LOGS_DIR, session_log_name)
cwd_log_path = os.path.join("logs", session_log_name)

log_formatter = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

file_handler = logging.FileHandler(session_log_path, encoding="utf-8")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(log_formatter)

logging.basicConfig(level=logging.INFO)
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

if not any(isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == os.path.abspath(session_log_path) for h in root_logger.handlers):
    root_logger.addHandler(file_handler)

if os.path.abspath(cwd_log_path) != os.path.abspath(session_log_path):
    cwd_handler = logging.FileHandler(cwd_log_path, encoding="utf-8")
    cwd_handler.setLevel(logging.INFO)
    cwd_handler.setFormatter(log_formatter)
    if not any(isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == os.path.abspath(cwd_log_path) for h in root_logger.handlers):
        root_logger.addHandler(cwd_handler)

logger = logging.getLogger("autonomous_strategist_api")
logger.info(f"Persistent session logging initialized. Active log: {session_log_path}")


# ---------------------------------------------------------------------------
# 1. Lifespan Event Handler (Database Init + Background Monitor + Autopilot)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize database & start background position evaluator and autonomous scanner
    logger.info("Initializing Paper Trading SQLite database...")
    init_db()
    logger.info("Starting background Position Monitor loop...")
    start_position_monitor()
    logger.info("Starting Autonomous Scanner loop...")
    start_autonomous_scanner()
    yield
    # Shutdown: Cleanly cancel background tasks
    logger.info("Halting Autonomous Scanner background loop...")
    stop_autonomous_scanner()
    logger.info("Halting Position Monitor background loop...")
    stop_position_monitor()


app = FastAPI(
    title="Autonomous Equity & Options Strategist API",
    description="Real-time multi-agent options trading pipeline with paper trading ledger, memory reflection, and autonomous execution.",
    version="2.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# 2. CORS Configuration
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permits React/Vite development on localhost:5173
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# 3. SSE Streaming Generator (LangGraph Orchestration)
# ---------------------------------------------------------------------------
async def sse_event_stream(ticker: str) -> AsyncGenerator[str, None]:
    """
    Executes compiled LangGraph using graph.stream() in an executor,
    yielding state updates in standard Server-Sent Events (SSE) format:
    data: {json_chunk}\n\n
    """
    clean_ticker = ticker.strip().upper()
    session_id = f"session_{clean_ticker}_{int(datetime.now(timezone.utc).timestamp())}"
    logger.info(f"Initiating SSE stream for {clean_ticker} (Session ID: {session_id})")

    current_state: StrategistState = {
        "ticker": clean_ticker,
        "messages": [
            {
                "agent": "System",
                "stage": "Initialization",
                "text": f"Pipeline initialized for {clean_ticker}. Starting live data ingestion...",
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
    }

    try:
        init_payload = {
            "session_id": session_id,
            "ticker": clean_ticker,
            "node": "system",
            "message": current_state["messages"][0],
            "messages": current_state["messages"],
            "risk_approved": False,
            "final_trade": {},
            "market_data": {},
            "rag_context": "",
            "quant_proposal": {},
            "revision_count": 0,
            "risk_feedback": "",
            "past_lessons": "",
        }
        yield f"data: {json.dumps(init_payload)}\n\n"

        loop = asyncio.get_event_loop()

        def run_stream():
            return list(graph.stream(current_state))

        stream_steps = await loop.run_in_executor(None, run_stream)

        for step in stream_steps:
            for node_name, update in step.items():
                if "messages" in update and update["messages"]:
                    current_state["messages"].extend(update["messages"])
                if "market_data" in update:
                    current_state["market_data"] = update["market_data"]
                if "rag_context" in update:
                    current_state["rag_context"] = update["rag_context"]
                if "quant_proposal" in update:
                    current_state["quant_proposal"] = update["quant_proposal"]
                if "risk_approved" in update:
                    current_state["risk_approved"] = update["risk_approved"]
                if "final_trade" in update:
                    current_state["final_trade"] = update["final_trade"]
                if "revision_count" in update:
                    current_state["revision_count"] = update["revision_count"]
                if "risk_feedback" in update:
                    current_state["risk_feedback"] = update["risk_feedback"]
                if "past_lessons" in update:
                    current_state["past_lessons"] = update["past_lessons"]
                if "execution_details" in update:
                    current_state["execution_details"] = update["execution_details"]

                latest_message = (
                    update["messages"][-1]
                    if ("messages" in update and update["messages"])
                    else None
                )

                payload = {
                    "session_id": session_id,
                    "ticker": clean_ticker,
                    "node": node_name,
                    "message": latest_message,
                    "messages": current_state["messages"],
                    "market_data": current_state["market_data"],
                    "rag_context": current_state["rag_context"],
                    "quant_proposal": current_state["quant_proposal"],
                    "risk_approved": current_state["risk_approved"],
                    "final_trade": current_state["final_trade"],
                    "revision_count": current_state.get("revision_count", 0),
                    "risk_feedback": current_state.get("risk_feedback", ""),
                    "past_lessons": current_state.get("past_lessons", ""),
                    "execution_details": current_state.get("execution_details"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }


                logger.info(f"Yielding SSE update from node: [{node_name}]")
                yield f"data: {json.dumps(payload)}\n\n"
                await asyncio.sleep(0.35)

        logger.info(f"Workflow stream completed successfully for {clean_ticker}")

    except Exception as exc:
        logger.error(f"Error in SSE stream for {clean_ticker}: {exc}", exc_info=True)
        error_payload = {
            "session_id": session_id,
            "ticker": clean_ticker,
            "node": "error",
            "error": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        yield f"data: {json.dumps(error_payload)}\n\n"


# ---------------------------------------------------------------------------
# 4. Endpoints
# ---------------------------------------------------------------------------
@app.get("/")
@app.get("/api")
def read_root():
    """Service metadata."""
    return {
        "service": "Autonomous Equity & Options Strategist",
        "version": "2.1.0",
        "status": "online",
        "endpoints": {
            "stream_live": "/api/stream/live",
            "stream_ticker": "/api/stream/{ticker}",
            "autopilot_status": "/api/autopilot/status",
            "autopilot_toggle": "POST /api/autopilot/toggle",
            "autopilot_scan": "POST /api/autopilot/scan",
            "portfolio": "/api/portfolio",
            "execute_trade": "POST /api/trade/execute",
            "history": "/api/positions/history",
            "close_position": "POST /api/positions/{id}/close",
            "lessons": "/api/lessons/{ticker}",
        },
    }


@app.get("/health")
@app.get("/api/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/stream/live")
async def stream_live_telemetry():
    """
    GET /api/stream/live (STEP 14)
    Unified Server-Sent Events (SSE) telemetry feed broadcasting autonomous agent decisions,
    scans, auto-executions, risk rejections, and auto-exits across the watchlist in real time.
    """
    async def sse_telemetry_publisher():
        try:
            async for event in subscribe_telemetry():
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        sse_telemetry_publisher(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream; charset=utf-8",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/autopilot/status")
def get_status():
    """
    GET /api/autopilot/status (STEP 13 & 14)
    Returns autonomous engine state: active status, market session, IST time, open positions, cooldowns.
    """
    return get_autopilot_status()


@app.post("/api/autopilot/toggle")
async def toggle_status(payload: Optional[Dict[str, Any]] = Body(default=None)):
    """
    POST /api/autopilot/toggle (STEP 13 & 14)
    Emergency Kill-Switch toggling IS_AUTOPILOT_ACTIVE (True/False).
    """
    desired = payload.get("active") if payload else None
    new_state = await toggle_autopilot(desired)
    return {
        "success": True,
        "active": new_state,
        "message": f"Autopilot is now {'ACTIVE (SCANNING)' if new_state else 'PAUSED'}",
    }


@app.post("/api/autopilot/scan")
async def trigger_scan(payload: Dict[str, Any] = Body(...)):
    """
    POST /api/autopilot/scan
    On-demand autonomous scan trigger for a selected ticker.
    """
    ticker = payload.get("ticker", "NIFTY").strip().upper()
    asyncio.create_task(scan_single_ticker(ticker, force=True))
    return {
        "success": True,
        "ticker": ticker,
        "message": f"Autonomous scan initiated for {ticker}.",
    }


@app.get("/api/stream/{ticker}")
async def stream_ticker_analysis(ticker: str):
    """
    GET /api/stream/{ticker}
    Server-Sent Events endpoint executing the multi-agent LangGraph workflow.
    """
    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Valid ticker symbol required.",
        )

    return StreamingResponse(
        sse_event_stream(clean_ticker),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream; charset=utf-8",
            "X-Accel-Buffering": "no",
        },
    )



# ---------------------------------------------------------------------------
# 5. Paper Trading & Portfolio Endpoints (STEP 9 & 10)
# ---------------------------------------------------------------------------
@app.get("/api/portfolio")
def get_portfolio():
    """
    GET /api/portfolio
    Returns virtual cash, margin used, realized P&L, win rate, and open positions with live MTM.
    """
    with Session(engine) as session:
        account = get_account(session)

        # Open positions
        statement = select(Position).where(Position.status == "OPEN")
        open_pos_records = session.exec(statement).all()

        # Closed positions for win-rate calculation
        closed_statement = select(Position).where(Position.status != "OPEN")
        closed_pos_records = session.exec(closed_statement).all()

        total_closed = len(closed_pos_records)
        winning_trades = sum(1 for p in closed_pos_records if (p.pnl or 0) > 0)
        win_rate = round((winning_trades / total_closed * 100.0), 1) if total_closed > 0 else 0.0

        # Compute real-time MTM for each open position
        open_positions: List[Dict[str, Any]] = []
        total_unrealized_pnl = 0.0

        for pos in open_pos_records:
            mtm_info = calculate_position_mtm(pos)
            unrealized = mtm_info["unrealized_pnl"]
            total_unrealized_pnl += unrealized

            open_positions.append({
                "id": pos.id,
                "ticker": pos.ticker,
                "strategy_name": pos.strategy_name,
                "legs": json.loads(pos.legs_json or "[]"),
                "net_premium": pos.net_premium,
                "capital_allocated": pos.capital_allocated,
                "current_mtm": mtm_info["current_mtm"],
                "gross_pnl": mtm_info.get("gross_pnl", unrealized),
                "unrealized_pnl": unrealized,
                "net_pnl": mtm_info.get("net_pnl", unrealized),
                "estimated_charges": mtm_info.get("estimated_charges", 0.0),
                "stop_loss": pos.stop_loss,
                "target": pos.target,
                "entry_timestamp": pos.entry_timestamp.isoformat() if pos.entry_timestamp else "",
                "status": pos.status,
                "thesis": pos.thesis,
            })

        return {
            "virtual_balance": round(account.virtual_balance, 2),
            "margin_used": round(account.margin_used, 2),
            "available_cash": round(account.virtual_balance - account.margin_used, 2),
            "realized_pnl": round(account.realized_pnl, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "net_worth": round(account.virtual_balance + total_unrealized_pnl, 2),
            "win_rate_pct": win_rate,
            "total_trades": total_closed,
            "winning_trades": winning_trades,
            "open_positions": open_positions,
        }


@app.post("/api/trade/execute")
def execute_trade(proposal: Dict[str, Any] = Body(...)):
    """
    POST /api/trade/execute
    Fills a paper trade proposal, allocating margin and creating an OPEN Position record.
    """
    if not proposal or not proposal.get("ticker"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Trade proposal must include ticker and option legs.",
        )

    res = execute_paper_order(proposal)
    if not res.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=res.get("message", "Execution failed due to margin constraints."),
        )

    return res


@app.get("/api/positions/history")
def get_positions_history():
    """
    GET /api/positions/history
    Returns all closed trades with entry, exit, duration, gross/net P&L, and charges.
    """
    with Session(engine) as session:
        statement = (
            select(Position)
            .where(Position.status != "OPEN")
            .order_by(Position.exit_timestamp.desc())
        )
        closed_records = session.exec(statement).all()

        history: List[Dict[str, Any]] = []
        for pos in closed_records:
            duration_minutes = 0
            if pos.entry_timestamp and pos.exit_timestamp:
                duration_minutes = round(
                    (pos.exit_timestamp - pos.entry_timestamp).total_seconds() / 60.0, 1
                )

            gross = pos.gross_pnl if pos.gross_pnl is not None else pos.pnl
            charges_val = pos.total_charges if pos.total_charges is not None else (pos.charges or 0.0)
            net = pos.net_pnl if pos.net_pnl is not None else pos.pnl
            if net is None and gross is not None:
                net = round(gross - (charges_val or 0.0), 2)

            history.append({
                "id": pos.id,
                "ticker": pos.ticker,
                "strategy_name": pos.strategy_name,
                "legs": json.loads(pos.legs_json or "[]"),
                "net_premium": pos.net_premium,
                "capital_allocated": pos.capital_allocated,
                "exit_price": pos.exit_price,
                "gross_pnl": gross,
                "total_charges": charges_val,
                "charges": charges_val,
                "net_pnl": net,
                "pnl": net,
                "status": pos.status,
                "entry_timestamp": pos.entry_timestamp.isoformat() if pos.entry_timestamp else "",
                "exit_timestamp": pos.exit_timestamp.isoformat() if pos.exit_timestamp else "",
                "duration_minutes": duration_minutes,
                "thesis": pos.thesis,
            })

        return history


@app.post("/api/positions/{position_id}/close")
def manual_close_position(position_id: int):
    """
    POST /api/positions/{id}/close
    Manually closes an active open position at current market rates.
    """
    res = close_position_by_id(position_id, reason="CLOSED_MANUAL")
    if not res.get("success"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=res.get("error"))
    return res


# ---------------------------------------------------------------------------
# 6. Learned Memory & Lessons Endpoint (STEP 11)
# ---------------------------------------------------------------------------
@app.get("/api/lessons/{ticker}")
def get_lessons_for_ticker(ticker: str):
    """
    GET /api/lessons/{ticker}
    Retrieves learned trading rules and retrospective post-mortems from ChromaDB.
    """
    clean_ticker = ticker.strip().upper()
    lessons_markdown = retrieve_relevant_lessons(clean_ticker, "options strategy", n_results=5)
    return {
        "ticker": clean_ticker,
        "lessons_markdown": lessons_markdown,
    }


# ---------------------------------------------------------------------------
# 7. Trading Buggy - Shadow Monitor & Signal Stream (STEP 15)
# ---------------------------------------------------------------------------
@app.get("/api/buggy/stream")
async def stream_buggy():
    """
    GET /api/buggy/stream (STEP 15)
    Server-Sent Events endpoint streaming real-time actionable signals (BUY_SIGNAL,
    REVERSAL_ALERT, SELL_SIGNAL) from the Trading Buggy shadow monitor.
    Operates in parallel without blocking LangGraph or autopilot execution.
    """
    return StreamingResponse(
        stream_buggy_signals(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream; charset=utf-8",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/buggy/overview")
def get_buggy_overview():
    """
    GET /api/buggy/overview
    Returns initial hydration data for the Trading Buggy Real-Money dashboard.
    """
    with Session(engine) as session:
        # Active open positions
        open_stmt = select(Position).where(Position.status == "OPEN")
        open_positions = session.exec(open_stmt).all()

        active_signals = []
        for pos in open_positions:
            legs = json.loads(pos.legs_json or "[]")
            bias = infer_strategy_bias(pos.strategy_name, pos.legs_json)
            strikes = [f"{l.get('strike')} {l.get('option_type')}" for l in legs]
            ticker_clean = (pos.ticker or "NIFTY").strip().upper()
            fallback_lot = get_lot_size(ticker_clean)
            lot_size = legs[0].get("lot_size", fallback_lot) if legs else fallback_lot
            qty = legs[0].get("qty", 1) if legs else 1

            active_signals.append({
                "type": "BUY_SIGNAL",
                "position_id": pos.id,
                "ticker": pos.ticker,
                "strategy_name": pos.strategy_name,
                "bias": bias,
                "legs": legs,
                "strikes": strikes,
                "recommended_entry_premium": abs(round(pos.net_premium, 2)),
                "net_premium": round(pos.net_premium, 2),
                "trade_type": "DEBIT" if pos.net_premium < 0 else "CREDIT",
                "lot_size": lot_size,
                "qty": qty,
                "total_quantity": lot_size * qty,
                "target": round(pos.target, 2),
                "stop_loss": round(pos.stop_loss, 2),
                "capital_allocated": round(pos.capital_allocated, 2),
                "entry_spot_price": round(float(legs[0].get("strike", 0.0) if legs else 0.0), 2),
                "entry_timestamp": pos.entry_timestamp.isoformat() if pos.entry_timestamp else "",
                "thesis": pos.thesis,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        # Recent completed signals (up to 15)
        closed_stmt = (
            select(Position)
            .where(Position.status != "OPEN")
            .order_by(Position.exit_timestamp.desc())
            .limit(15)
        )
        closed_positions = session.exec(closed_stmt).all()

        completed_signals = []
        for pos in closed_positions:
            reason_label = format_exit_reason(pos.status)
            bias = infer_strategy_bias(pos.strategy_name, pos.legs_json)

            completed_signals.append({
                "type": "SELL_SIGNAL",
                "position_id": pos.id,
                "ticker": pos.ticker,
                "strategy_name": pos.strategy_name,
                "bias": bias,
                "legs": json.loads(pos.legs_json or "[]"),
                "capital_allocated": round(pos.capital_allocated or 0.0, 2),
                "status": pos.status,
                "exit_reason": reason_label,
                "exit_price": round(pos.exit_price or 0.0, 2),
                "pnl": round(pos.pnl or 0.0, 2),
                "entry_timestamp": pos.entry_timestamp.isoformat() if pos.entry_timestamp else "",
                "exit_timestamp": pos.exit_timestamp.isoformat() if pos.exit_timestamp else "",
                "instruction": f"TRADE SIGNAL EXIT: {reason_label}. Square off your position in Demat.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        return {
            "active_signals": active_signals,
            "completed_signals": completed_signals,
            "total_active": len(active_signals),
            "total_completed": len(completed_signals),
        }


# ---------------------------------------------------------------------------
# Direct Launch
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    print("\n=======================================================")
    print(" Autonomous Equity & Options Strategist Backend v2.0")
    print(" Stream Endpoint: http://localhost:8000/api/stream/{ticker}")
    print(" Portfolio Endpoint: http://localhost:8000/api/portfolio")
    print(" Docs: http://localhost:8000/docs")
    print("=======================================================\n")
    uvicorn.run(app, host="127.0.0.1", port=8000)
