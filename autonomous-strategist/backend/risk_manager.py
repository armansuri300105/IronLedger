"""
Deterministic Regime & Momentum Gatekeeper
File: backend/risk_manager.py

Implements SYSTEM UPGRADE 31:
- Block Sideways Regimes:
    * Hard validation check for any proposed Bull Call Spread or Bear Put Spread.
    * Instantly rejects if market regime or flow is evaluated as CHOPPY_SIDEWAYS, NEUTRAL_FLOW, or FLAT.
- Strict Momentum & EMA Requirements (NIFTY & BANKNIFTY):
    * Rejects directional spreads if abs(EMA 9 - EMA 20) < 5.0 points.
    * Rejects directional spreads if RSI is in the neutral zone (between 47 and 53).
- Time-of-Day Filter (Afternoon Chop Protection):
    * If current time is after 13:30 IST, rejects all new directional spread entries
      UNLESS 5m momentum exceeds high-conviction threshold (> +30 pts for Bullish, < -30 pts for Bearish).
- Structured, human-readable rejection logging before execution dispatch.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("uvicorn.error")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

BLOCKED_REGIMES = {"CHOPPY_SIDEWAYS", "FLAT"}
BLOCKED_FLOWS = {"FLAT", "CHOPPY_SIDEWAYS"}
INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
IST_OFFSET = timezone(timedelta(hours=5, minutes=30))

# UPGRADE 36: Track last closed trade regime per symbol
LAST_CLOSED_REGIME: Dict[str, Dict[str, Any]] = {}

# UPGRADE 39: Track consecutive trend scans per symbol in memory
CONSECUTIVE_TREND_SCANS: Dict[str, Dict[str, Any]] = {}

# UPGRADE 48 & 49: Shared in-memory market state tracking latest Spot, Trend, 5m_move, 3m_move, ER, PCR, and RSI for NIFTY and BANKNIFTY
LATEST_MARKET_STATE: Dict[str, Dict[str, Any]] = {}


def update_market_state(
    ticker: str,
    trend: str = "CHOPPY_SIDEWAYS",
    move_5m: float = 0.0,
    er: float = 0.5,
    pcr: float = 1.0,
    spot: float = 0.0,
    move_3m: float = 0.0,
    rsi: float = 50.0,
    updated_at: Optional[datetime] = None,
):
    """
    UPGRADE 48 & 49: Updates shared LATEST_MARKET_STATE dict in memory
    with Spot, Trend, 5m_move, 3m_move, ER, PCR, RSI, and updated_at timestamp.
    """
    clean = (ticker or "NIFTY").strip().upper()
    now_utc = updated_at or datetime.now(timezone.utc)
    LATEST_MARKET_STATE[clean] = {
        "spot": float(spot or 0.0),
        "trend": (trend or "CHOPPY_SIDEWAYS").strip().upper(),
        "5m_move": float(move_5m or 0.0),
        "3m_move": float(move_3m or 0.0),
        "er": float(er or 0.5),
        "pcr": float(pcr or 1.0),
        "rsi": float(rsi or 50.0),
        "updated_at": now_utc,
        "timestamp": now_utc,
    }


def get_market_state(ticker: str) -> Dict[str, Any]:
    """Retrieves latest market state for a symbol."""
    clean = (ticker or "NIFTY").strip().upper()
    return LATEST_MARKET_STATE.get(clean, {})


def reset_market_state(ticker: Optional[str] = None):
    """Resets market state dict for testing."""
    if ticker:
        LATEST_MARKET_STATE.pop((ticker or "").strip().upper(), None)
    else:
        LATEST_MARKET_STATE.clear()


def is_cross_index_macro_divergent(
    ticker: str,
    strategy_name: str,
    market_bias: str = "",
) -> Tuple[bool, Optional[str]]:
    """
    UPGRADE 48 & 49: Cross-Index Macro Alignment Guard with Freshness Guarantee.
    Blocks counter-trend divergence before entry:
    - Do NOT allow NIFTY to enter a Bear Put Spread when BANKNIFTY is in STRONG_UPTREND with 5m_move > +20.0
    - Do NOT allow NIFTY to enter a Bull Call Spread when BANKNIFTY is in STRONG_DOWNTREND with 5m_move < -20.0
    - And vice versa for BANKNIFTY against NIFTY.
    - If other index telemetry is missing or older than 45 seconds, refreshes fresh metrics immediately.
    """
    clean = (ticker or "NIFTY").strip().upper()
    other_sym = "BANKNIFTY" if clean == "NIFTY" else ("NIFTY" if clean == "BANKNIFTY" else None)
    if not other_sym:
        return False, None

    other_state = LATEST_MARKET_STATE.get(other_sym, {})

    # UPGRADE 49: If other index telemetry is missing or older than 45s, refresh immediately
    updated_at = other_state.get("updated_at") or other_state.get("timestamp")
    is_stale = False
    if not other_state or not updated_at:
        is_stale = True
    else:
        if isinstance(updated_at, datetime):
            ts = updated_at if updated_at.tzinfo is not None else updated_at.replace(tzinfo=timezone.utc)
            age_sec = (datetime.now(timezone.utc) - ts).total_seconds()
        else:
            age_sec = time.time() - float(updated_at)
        if age_sec > 45.0:
            is_stale = True

    if is_stale:
        try:
            from market_data import fetch_live_options_data
            fresh_data = fetch_live_options_data(other_sym, force_refresh=True)
            if fresh_data and isinstance(fresh_data, dict):
                spot = float(fresh_data.get("spot_price", 0.0) or 0.0)
                pcr = float(fresh_data.get("pcr", 1.0) or 1.0)
                tech = fresh_data.get("intraday_technicals", {}) if isinstance(fresh_data.get("intraday_technicals"), dict) else {}
                t_state = str(fresh_data.get("trend_state") or tech.get("trend_state", "CHOPPY_SIDEWAYS"))
                m5 = float(fresh_data.get("move_5m_pts") or tech.get("move_5m_pts", 0.0) or 0.0)
                m3 = float(fresh_data.get("move_3m_pts") or tech.get("move_3m_pts", 0.0) or 0.0)
                er = float(fresh_data.get("efficiency_ratio") or tech.get("efficiency_ratio", 0.5) or 0.5)
                rsi = float(tech.get("rsi_14", tech.get("rsi", fresh_data.get("rsi_14", 50.0))) or 50.0)
                update_market_state(other_sym, t_state, m5, er, pcr, spot=spot, move_3m=m3, rsi=rsi)
                other_state = LATEST_MARKET_STATE.get(other_sym, {})
        except Exception as exc:
            logger.debug(f"[GATEKEEPER] Could not auto-refresh stale {other_sym} telemetry: {exc}")

    o_trend = str(other_state.get("trend", "")).strip().upper()
    o_m5 = float(other_state.get("5m_move", 0.0) or 0.0)

    bias = get_directional_bias(strategy_name, market_bias)
    strat_lower = (strategy_name or "").lower()
    is_bear_put = ("bear" in strat_lower and "put" in strat_lower) or bias == "BEARISH"
    is_bull_call = ("bull" in strat_lower and "call" in strat_lower) or bias == "BULLISH"

    # Do NOT allow Bear Put Spread when other index is in STRONG_UPTREND with 5m_move > +20.0
    if is_bear_put and o_trend == "STRONG_UPTREND" and o_m5 > 20.0:
        reason = (
            f"REJECTED: Cross-index macro alignment conflict: {clean} {strategy_name} blocked "
            f"because {other_sym} is in STRONG_UPTREND with 5m_move={o_m5:+.2f} > +20.0 pts."
        )
        logger.info(f"[GATEKEEPER] {clean} {strategy_name} -> {reason}")
        return True, reason

    # Do NOT allow Bull Call Spread when other index is in STRONG_DOWNTREND with 5m_move < -20.0
    if is_bull_call and o_trend == "STRONG_DOWNTREND" and o_m5 < -20.0:
        reason = (
            f"REJECTED: Cross-index macro alignment conflict: {clean} {strategy_name} blocked "
            f"because {other_sym} is in STRONG_DOWNTREND with 5m_move={o_m5:+.2f} < -20.0 pts."
        )
        logger.info(f"[GATEKEEPER] {clean} {strategy_name} -> {reason}")
        return True, reason

    return False, None


def check_anti_exhaustion_entry_guard(
    ticker: str,
    strategy_name: str,
    move_5m: float,
    rsi: float,
    market_bias: str = "",
) -> Tuple[bool, Optional[str]]:
    """
    UPGRADE 49: Opening-Range & Anti-Exhaustion Entry Guard.
    Never enter a Bull Call Spread if 5m_move has already run > +45.0 pts on NIFTY
    (> +135.0 pts on BANKNIFTY) without a pullback (RSI > 68).
    Never enter a Bear Put Spread if 5m_move has already dropped < -45.0 pts on NIFTY
    (< -135.0 pts on BANKNIFTY) without a bounce (RSI < 32).
    """
    clean_ticker = (ticker or "NIFTY").strip().upper()
    bias = get_directional_bias(strategy_name, market_bias)
    thresh = 135.0 if clean_ticker == "BANKNIFTY" else 45.0

    strat_lower = (strategy_name or "").lower()
    is_bull = bias == "BULLISH" or ("bull" in strat_lower and "call" in strat_lower)
    is_bear = bias == "BEARISH" or ("bear" in strat_lower and "put" in strat_lower)

    if is_bull and move_5m > thresh and rsi > 68.0:
        reason = (
            f"REJECTED: Anti-Exhaustion Entry Guard: {clean_ticker} {strategy_name} blocked "
            f"because 5m_move={move_5m:+.2f} pts > +{thresh:.1f} pts with overbought RSI={rsi:.1f} > 68.0 "
            f"without a pullback."
        )
        return True, reason

    if is_bear and move_5m < -thresh and rsi < 32.0:
        reason = (
            f"REJECTED: Anti-Exhaustion Entry Guard: {clean_ticker} {strategy_name} blocked "
            f"because 5m_move={move_5m:+.2f} pts < -{thresh:.1f} pts with oversold RSI={rsi:.1f} < 32.0 "
            f"without a bounce."
        )
        return True, reason

    return False, None

    return False, None


def record_trend_scan(ticker: str, trend_state: str) -> int:
    """
    UPGRADE 39: Records a live scan trend regime for a symbol and increments
    consecutive scan counter if the trend regime persists.
    """
    clean = (ticker or "NIFTY").strip().upper()
    trend = (trend_state or "CHOPPY_SIDEWAYS").strip().upper()
    prev = CONSECUTIVE_TREND_SCANS.get(clean, {})
    prev_trend = prev.get("regime", "")
    prev_count = int(prev.get("count", 0))

    if trend in ("STRONG_UPTREND", "STRONG_DOWNTREND") and trend == prev_trend:
        new_count = prev_count + 1
    elif trend in ("STRONG_UPTREND", "STRONG_DOWNTREND"):
        new_count = 1
    else:
        new_count = 0

    CONSECUTIVE_TREND_SCANS[clean] = {
        "regime": trend,
        "count": new_count,
        "timestamp": datetime.now(timezone.utc),
    }
    return new_count


def get_consecutive_trend_scans(ticker: str, trend_state: Optional[str] = None) -> int:
    """Retrieves current consecutive scan count for a symbol in a trend regime."""
    clean = (ticker or "NIFTY").strip().upper()
    info = CONSECUTIVE_TREND_SCANS.get(clean, {})
    if trend_state:
        trend = trend_state.strip().upper()
        if info.get("regime") == trend:
            return int(info.get("count", 0))
        return 0
    return int(info.get("count", 0))


def reset_consecutive_trend_scans(ticker: str):
    """Resets consecutive trend scans for testing or end-of-day."""
    clean = (ticker or "NIFTY").strip().upper()
    CONSECUTIVE_TREND_SCANS.pop(clean, None)


def get_most_recent_closed_trade_today(ticker: str, session: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """
    UPGRADE 39: Queries SQLite positions table for the most recently closed trade on ticker today.
    Returns dict with {id, realized_pnl, is_loss, exited_in_chop, status, exit_timestamp} or None.
    """
    clean = (ticker or "NIFTY").strip().upper()
    now_ist = datetime.now(timezone.utc).astimezone(IST_OFFSET)
    today_start_utc = now_ist.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

    def _query(sess):
        from database import Position
        from sqlmodel import select
        stmt = (
            select(Position)
            .where(Position.ticker == clean, Position.status != "OPEN")
            .order_by(Position.exit_timestamp.desc(), Position.id.desc())
        )
        pos_list = sess.exec(stmt).all()
        for p in pos_list:
            exit_ts = p.exit_timestamp or p.entry_timestamp
            if exit_ts:
                if exit_ts.tzinfo is None:
                    exit_ts = exit_ts.replace(tzinfo=timezone.utc)
                if exit_ts >= today_start_utc:
                    realized_pnl = p.net_pnl if p.net_pnl is not None else (p.gross_pnl if p.gross_pnl is not None else p.pnl)
                    thesis_text = (p.thesis or "").upper()
                    status_text = (p.status or "").upper()
                    exited_in_chop = "CHOPPY_SIDEWAYS" in thesis_text or "CHOPPY" in status_text or (status_text == "CLOSED_TREND_REVERSAL" and "CHOPPY" in thesis_text)
                    is_loss = (realized_pnl is not None and realized_pnl < 0)
                    return {
                        "id": p.id,
                        "realized_pnl": realized_pnl,
                        "is_loss": is_loss,
                        "exited_in_chop": exited_in_chop,
                        "status": p.status,
                        "exit_timestamp": exit_ts,
                    }
        return None

    if session is not None:
        return _query(session)
    try:
        from database import engine
        from sqlmodel import Session
        with Session(engine) as sess:
            return _query(sess)
    except Exception as exc:
        logger.debug(f"Error querying last closed trade today: {exc}")
        return None


def record_closed_trade_regime(ticker: str, regime: str, exit_data: Optional[Dict[str, Any]] = None):
    """Records the exit regime and metadata of the most recently closed trade for a symbol."""
    clean = (ticker or "NIFTY").strip().upper()
    LAST_CLOSED_REGIME[clean] = {
        "regime": (regime or "").strip().upper(),
        "timestamp": datetime.now(timezone.utc),
        **(exit_data or {}),
    }


def get_last_closed_regime(ticker: str) -> Optional[str]:
    """Retrieves the most recent closed trade regime for a symbol."""
    clean = (ticker or "NIFTY").strip().upper()
    if clean in LAST_CLOSED_REGIME:
        return LAST_CLOSED_REGIME[clean].get("regime")
    try:
        from database import engine, Position
        from sqlmodel import Session, select
        with Session(engine) as session:
            pos = session.exec(
                select(Position)
                .where(Position.ticker == clean, Position.status != "OPEN")
                .order_by(Position.exit_timestamp.desc())
            ).first()
            if pos and pos.thesis:
                for r in ["STRONG_DOWNTREND", "STRONG_UPTREND", "CHOPPY_SIDEWAYS"]:
                    if r in pos.thesis.upper():
                        return r
    except Exception:
        pass
    return None


def is_directional_spread(strategy_name: str, market_bias: str = "") -> bool:
    """
    Determines whether a proposed strategy is a directional spread
    (e.g., Bull Call Spread, Bear Put Spread).
    Excludes non-directional / market-neutral setups like Iron Condor, Straddle, or Strangle.
    """
    name = (strategy_name or "").lower()
    bias = (market_bias or "").lower()

    # Explicit Bull Call or Bear Put check
    if "bull call" in name or "bear put" in name:
        return True
    if "bull" in name and "call" in name and "spread" in name:
        return True
    if "bear" in name and "put" in name and "spread" in name:
        return True

    # General directional spreads, strictly excluding neutral setups
    if any(s in name for s in ["call spread", "put spread"]) and not any(
        s in name for s in ["condor", "straddle", "strangle", "iron"]
    ):
        return True

    if "spread" in name and bias in ("bullish", "bearish") and not any(
        s in name for s in ["condor", "straddle", "strangle", "iron"]
    ):
        return True

    return False


def get_directional_bias(strategy_name: str, market_bias: str = "") -> str:
    """
    Infers directional bias ('BULLISH', 'BEARISH', or 'NEUTRAL') from strategy name and market bias.
    """
    name = (strategy_name or "").lower()
    bias = (market_bias or "").lower()

    if "bull" in name or "call spread" in name or bias == "bullish":
        return "BULLISH"
    if "bear" in name or "put spread" in name or bias == "bearish":
        return "BEARISH"
    return "NEUTRAL"


def validate_trading_window(now_ist: Optional[datetime] = None) -> Tuple[bool, str]:
    """
    UPGRADE 50 Rule 1: Trading Window (09:45–14:45 IST)
    Blocks all new trade entries before 09:45:00 IST (opening range settling)
    and after 14:45:00 IST.
    """
    if now_ist is None:
        import os
        force = os.getenv("FORCE_MARKET_OPEN", "").strip().lower() in ("true", "1", "yes")
        if force:
            return True, ""
        now_ist = datetime.now(timezone.utc).astimezone(IST_OFFSET)
    elif now_ist.tzinfo is None:
        now_ist = now_ist.replace(tzinfo=IST_OFFSET)

    t = (now_ist.hour, now_ist.minute)
    if t < (9, 45):
        return False, (
            f"Trading Window Restriction: Current time is {now_ist.strftime('%H:%M')} IST. "
            f"New trade entries blocked before 09:45:00 IST to let the 30-minute opening range settle."
        )
    if t >= (14, 45):
        return False, (
            f"Trading Window Restriction: Current time is {now_ist.strftime('%H:%M')} IST. "
            f"New trade entries blocked after 14:45:00 IST."
        )
    return True, ""


def validate_daily_trade_limits(ticker: str, session: Optional[Any] = None) -> Tuple[bool, str]:
    """
    UPGRADE 50 Rule 1: Enforce hard cap of max 3 executed trades per symbol per day
    (max 6 trades/day total across NIFTY and BANKNIFTY).
    """
    import database
    from database import count_today_trades, MAX_DAILY_TOTAL_TRADES, MAX_DAILY_SYMBOL_TRADES
    clean_sym = ticker.strip().upper()
    target_engine = getattr(database, "engine", None)

    def _eval(sess):
        total_count = count_today_trades(sess)
        if total_count >= 6:
            return False, f"Daily total trade cap reached: {total_count} executed trades today (max 6/day across indices)."
        sym_count = count_today_trades(sess, clean_sym)
        if sym_count >= 3:
            return False, f"Daily symbol trade cap reached: {clean_sym} has {sym_count} executed trades today (max 3/symbol/day)."
        return True, ""

    if session is not None:
        return _eval(session)
    else:
        from sqlmodel import Session
        with Session(target_engine) as sess:
            return _eval(sess)


def validate_symbol_cooldown(ticker: str, session: Optional[Any] = None) -> Tuple[bool, str]:
    """
    UPGRADE 50 Rule 1: Mandatory 20-minute (1200s) cooldown per symbol after any position closes.
    """
    import database
    from database import Position
    from sqlmodel import select, Session
    clean_sym = ticker.strip().upper()
    now_utc = datetime.now(timezone.utc)
    target_engine = getattr(database, "engine", None)

    def _eval(sess):
        closed_positions = sess.exec(
            select(Position)
            .where(Position.ticker == clean_sym, Position.status != "OPEN")
            .order_by(Position.exit_timestamp.desc(), Position.id.desc())
        ).all()
        if not closed_positions:
            return True, ""
        last_pos = closed_positions[0]
        exit_time = last_pos.exit_timestamp or last_pos.entry_timestamp or now_utc
        if exit_time.tzinfo is None:
            exit_time = exit_time.replace(tzinfo=timezone.utc)
        elapsed_sec = (now_utc - exit_time).total_seconds()
        if elapsed_sec < 1200.0:
            rem_sec = 1200.0 - elapsed_sec
            return False, (
                f"Mandatory 20-minute cooldown active for {clean_sym}: Position #{last_pos.id} "
                f"closed {elapsed_sec/60.0:.1f}m ago ({last_pos.status}). "
                f"New entries blocked for {rem_sec/60.0:.1f} mins remaining."
            )
        return True, ""

    if session is not None:
        return _eval(session)
    else:
        with Session(target_engine) as sess:
            return _eval(sess)


def check_triple_confluence_entry_gate(
    ticker: str,
    strategy_or_bias: str,
    market_data: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    UPGRADE 50 Rule 2: Strict Triple-Confluence Entry Gate
    Refreshes LATEST_MARKET_STATE and only approves when all 4 conditions hold simultaneously:

    Bull Call Spread:
      1) Trend == "STRONG_UPTREND" and ER >= 0.45,
      2) EMA_9 > EMA_20 and Spot > EMA_9,
      3) 5m_move >= +15.0 pts on NIFTY (>= +45.0 pts on BANKNIFTY) and 3m_move > 0,
      4) 52.0 <= RSI <= 70.0 (not exhausted) and the other index is NOT in STRONG_DOWNTREND.

    Bear Put Spread:
      1) Trend == "STRONG_DOWNTREND" and ER >= 0.45,
      2) EMA_9 < EMA_20 and Spot < EMA_9,
      3) 5m_move <= -15.0 pts on NIFTY (<= -45.0 pts on BANKNIFTY) and 3m_move < 0,
      4) 30.0 <= RSI <= 48.0 (not oversold) and the other index is NOT in STRONG_UPTREND.

    If not met, returns (False, failure_reason).
    """
    clean_ticker = (ticker or "NIFTY").strip().upper()
    technicals = (
        market_data.get("intraday_technicals", {})
        if isinstance(market_data.get("intraday_technicals"), dict)
        else {}
    )

    trend = str(
        market_data.get("trend_state")
        or market_data.get("trend")
        or technicals.get("trend_state")
        or technicals.get("trend")
        or market_data.get("regime")
        or ""
    ).strip().upper()
    er = float(technicals.get("efficiency_ratio", market_data.get("efficiency_ratio", 0.0)) or 0.0)
    ema_9 = float(technicals.get("ema_9", market_data.get("ema_9", 0.0)) or 0.0)
    ema_20 = float(technicals.get("ema_20", market_data.get("ema_20", 0.0)) or 0.0)
    spot = float(market_data.get("spot_price", 0.0) or 0.0)
    m5 = float(market_data.get("move_5m_pts") or technicals.get("move_5m_pts", 0.0) or 0.0)
    m3 = float(market_data.get("move_3m_pts") or technicals.get("move_3m_pts", 0.0) or 0.0)

    rsi_val = (
        technicals.get("rsi_14")
        or technicals.get("rsi")
        or market_data.get("rsi_14")
        or market_data.get("rsi")
    )
    rsi = float(rsi_val if rsi_val is not None else 50.0)

    other_sym = "BANKNIFTY" if clean_ticker == "NIFTY" else ("NIFTY" if clean_ticker == "BANKNIFTY" else None)
    other_trend = str(market_data.get("other_index_trend", "")).strip().upper()
    if other_sym and not other_trend:
        other_state = get_market_state(other_sym)
        other_trend = str(other_state.get("trend", "")).strip().upper()

    strat_lower = (strategy_or_bias or "").lower()
    is_bullish = any(w in strat_lower for w in ["bull", "call", "uptrend"])
    is_bearish = any(w in strat_lower for w in ["bear", "put", "downtrend"])

    m5_thresh = 45.0 if clean_ticker == "BANKNIFTY" else 15.0

    if is_bullish:
        failures = []
        if trend != "STRONG_UPTREND" or er < 0.45:
            failures.append(f"Trend must be STRONG_UPTREND with ER >= 0.45 (got {trend}, ER={er:.2f})")
        if not (ema_9 > ema_20 and spot > ema_9):
            failures.append(f"EMA confluence required: EMA_9 ({ema_9:.1f}) > EMA_20 ({ema_20:.1f}) and Spot ({spot:.1f}) > EMA_9 ({ema_9:.1f})")
        if not (m5 >= m5_thresh and m3 > 0):
            failures.append(f"Momentum required: 5m_move ({m5:+.1f}) >= +{m5_thresh:.1f} pts and 3m_move ({m3:+.1f}) > 0")
        if not (52.0 <= rsi <= 70.0):
            failures.append(f"RSI within [52.0, 70.0] required (got {rsi:.1f})")
        if other_trend == "STRONG_DOWNTREND":
            failures.append(f"Cross-Index Conflict: other index {other_sym} is in STRONG_DOWNTREND")

        if failures:
            return False, f"Bull Call Confluence failed: {'; '.join(failures)}"
        return True, "TRIPLE_CONFLUENCE_CONFIRMED: Bull Call Confluence verified"

    elif is_bearish:
        failures = []
        if trend != "STRONG_DOWNTREND" or er < 0.45:
            failures.append(f"Trend must be STRONG_DOWNTREND with ER >= 0.45 (got {trend}, ER={er:.2f})")
        if not (ema_9 < ema_20 and spot < ema_9):
            failures.append(f"EMA confluence required: EMA_9 ({ema_9:.1f}) < EMA_20 ({ema_20:.1f}) and Spot ({spot:.1f}) < EMA_9 ({ema_9:.1f})")
        if not (m5 <= -m5_thresh and m3 < 0):
            failures.append(f"Momentum required: 5m_move ({m5:+.1f}) <= -{m5_thresh:.1f} pts and 3m_move ({m3:+.1f}) < 0")
        if not (30.0 <= rsi <= 48.0):
            failures.append(f"RSI within [30.0, 48.0] required (got {rsi:.1f})")
        if other_trend == "STRONG_UPTREND":
            failures.append(f"Cross-Index Conflict: other index {other_sym} is in STRONG_UPTREND")

        if failures:
            return False, f"Bear Put Confluence failed: {'; '.join(failures)}"
        return True, "TRIPLE_CONFLUENCE_CONFIRMED: Bear Put Confluence verified"

    return False, f"Invalid directional bias '{strategy_or_bias}'. Only Bull Call Spread and Bear Put Spread permitted."


def validate_strict_regime_gatekeeper(
    ticker: str,
    strategy_name: str,
    market_data: Dict[str, Any],
    market_bias: str = "",
    current_time: Optional[datetime] = None,
) -> Tuple[bool, Optional[str]]:
    """
    UPGRADE 50 Rule 2: Rigid Institutional Gatekeeper
    Only approves Bull Call Spread or Bear Put Spread meeting exact triple-confluence entry rules.
    """
    clean_ticker = (ticker or "NIFTY").strip().upper()

    # Rule 1: Trading Window Check (09:45 - 14:45 IST)
    window_ok, window_msg = validate_trading_window(current_time)
    if not window_ok:
        return False, window_msg

    # UPGRADE 48: Cross-Index Macro Alignment Guard
    is_macro_div, macro_reason = is_cross_index_macro_divergent(clean_ticker, strategy_name, market_bias)
    if is_macro_div:
        return False, macro_reason

    # Only directional spreads (Bull Call / Bear Put) are gated
    if not is_directional_spread(strategy_name, market_bias):
        return True, None

    technicals = (
        market_data.get("intraday_technicals", {})
        if isinstance(market_data.get("intraday_technicals"), dict)
        else {}
    )

    # UPGRADE 49: Opening-Range & Anti-Exhaustion Entry Guard
    m5_val = float(market_data.get("move_5m_pts") or technicals.get("move_5m_pts", 0.0) or 0.0)
    rsi_val_pre = technicals.get("rsi_14", technicals.get("rsi", market_data.get("rsi_14", None)))
    rsi_guard_val = float(rsi_val_pre if rsi_val_pre is not None else 50.0)
    is_exhausted, exhaust_reason = check_anti_exhaustion_entry_guard(
        clean_ticker, strategy_name, m5_val, rsi_guard_val, market_bias
    )
    if is_exhausted:
        logger.info(f"[GATEKEEPER] {clean_ticker} {strategy_name} -> {exhaust_reason}")
        return False, exhaust_reason

    # Rule 2: Strict Triple-Confluence Gate
    confluence_ok, confluence_msg = check_triple_confluence_entry_gate(
        clean_ticker, strategy_name or market_bias, market_data
    )
    if not confluence_ok:
        logger.info(f"[GATEKEEPER] {clean_ticker} {strategy_name} -> {confluence_msg}")
        return False, confluence_msg

    return True, None
