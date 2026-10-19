"""
Cloudflare Clef Gatekeeper & Intraday Momentum Detector
File: backend/clef_gatekeeper.py

Implements STEP 18, STEP 19 & STEP 20:
- Evaluates live options chains & PCR ratios using Cloudflare's Clef model (@cf/cloudflare/clef-flash).
- In-memory tick snapshot history (PREVIOUS_TICKS) tracking intraday spot velocity, PCR shift, and OI imbalance.
- STEP 20 Anti-Whipsaw & Anti-Chasing Guards:
    * Hard Chop Block: if is_choppy or ER < 0.38, blocks trade immediately.
    * Anti-Chasing Block: blocks RSI > 72 (overbought) or RSI < 28 (oversold).
    * OI Alignment Check for Stock Options: requires price-OI synchronization on equities.
- Rule 1 — Instant Technical & OI Breakout Trigger (Fast-Path):
    * NIFTY: abs(move_3m_pts) >= 15 OR abs(move_5m_pts) >= 22
    * BANKNIFTY: abs(move_3m_pts) >= 35 OR abs(move_5m_pts) >= 50
    * Equities: abs(move_5m_pct) >= 0.25%
    * Any ticker: consecutive_green_candles >= 3 OR consecutive_red_candles >= 3 OR candle_range_expansion >= 2.2
- Rule 2 — Calibrated Clef Evaluation:
    * Passes intraday_technicals and real NSE OI structure in Clef state
    * Threshold: True if prob >= 0.22 (22%) OR score >= 1.05
- Deterministic fallback when Cloudflare API encounters errors or missing keys.
"""

from __future__ import annotations

from collections import namedtuple
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
import requests
from sqlmodel import Session

from database import count_today_trades, engine

# Load environment variables from backend/.env or current working directory
dotenv_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path)
load_dotenv()

# Use Uvicorn logger so all gatekeeper logs appear directly in the server console
logger = logging.getLogger("uvicorn.error")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()

INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}

# Named tuple for structured, tuple-compatible snapshot tracking
TickSnapshot = namedtuple(
    "TickSnapshot",
    ["timestamp", "spot_price", "pcr", "atm_ce_oi", "atm_pe_oi"],
)

# In-memory dictionary keyed by ticker storing up to 10 recent snapshots
PREVIOUS_TICKS: Dict[str, List[TickSnapshot]] = {}


def evaluate_trade_opportunity(ticker: str, market_data_json: Dict[str, Any]) -> bool:
    """
    Evaluates intraday momentum and options structure using Cloudflare Clef model
    (@cf/cloudflare/clef-flash) with 1-minute candlestick technicals, real NSE OI walls,
    anti-whipsaw Kaufman efficiency filter, and fast-path breakout triggers.

    Args:
        ticker: The instrument symbol (e.g. "NIFTY", "BANKNIFTY", "RELIANCE").
        market_data_json: Live market payload containing spot_price, pcr, strikes Greeks,
                         intraday_technicals, and real_oi_structure.

    Returns:
        bool: True if actionable momentum / breakout setup is detected, False otherwise.
    """
    clean_ticker = (ticker or "NIFTY").strip().upper()
    if not market_data_json:
        logger.warning(f"[GATEKEEPER] No market data provided for {clean_ticker}. Skipping trade evaluation.")
        return False

    # -----------------------------------------------------------------------
    # UPGRADE 5 GUARD 0: Anti-Churn Overtrading Circuit Breakers
    # - Hard cap at 4 executed trades per session (max 2 per symbol)
    # - 15-minute post-exit cooldown per symbol
    # -----------------------------------------------------------------------
    try:
        from position_monitor import LAST_EXIT_TIMESTAMP
        last_exit = LAST_EXIT_TIMESTAMP.get(clean_ticker)
        if last_exit:
            elapsed_minutes = (datetime.now(timezone.utc) - last_exit).total_seconds() / 60.0
            if elapsed_minutes < 15.0:
                logger.info(
                    f"[GATEKEEPER] {clean_ticker} in post-exit cooldown ({15.0 - elapsed_minutes:.1f}m remaining). "
                    f"Blocking scan to prevent immediate overtrading churn."
                )
                return False
    except Exception:
        pass

    try:
        with Session(engine) as session:
            today_total = count_today_trades(session)
            today_ticker = count_today_trades(session, clean_ticker)
            if today_total >= 4:
                logger.info(
                    f"[GATEKEEPER] Anti-Churn Breaker: Daily session cap of 4 trades reached ({today_total}/4). Blocking scan."
                )
                return False
            if today_ticker >= 2:
                logger.info(
                    f"[GATEKEEPER] Anti-Churn Breaker: Daily cap of 2 trades for {clean_ticker} reached ({today_ticker}/2). Blocking scan."
                )
                return False
    except Exception as exc:
        logger.warning(f"[GATEKEEPER] Failed checking daily trade count: {exc}")

    spot = float(market_data_json.get("spot_price", 0.0))
    pcr = float(market_data_json.get("pcr", 1.0))
    volume_pcr = float(market_data_json.get("volume_pcr", 1.0))
    atm_put_oi_change = int(market_data_json.get("atm_put_oi_change", 0))
    atm_call_oi_change = int(market_data_json.get("atm_call_oi_change", 0))
    call_resistance_strike = market_data_json.get("call_resistance_strike", 0)
    put_support_strike = market_data_json.get("put_support_strike", 0)
    intraday_oi_pcr = float(market_data_json.get("intraday_oi_pcr", 1.0))
    short_covering_signal = str(market_data_json.get("short_covering_signal", "NEUTRAL_FLOW"))

    # 1-minute candlestick technicals & Step 20 anti-whipsaw metrics
    intraday_technicals = market_data_json.get("intraday_technicals", {})
    move_1m_pts = float(intraday_technicals.get("move_1m_pts", 0.0))
    move_3m_pts = float(intraday_technicals.get("move_3m_pts", 0.0))
    move_5m_pts = float(intraday_technicals.get("move_5m_pts", 0.0))
    move_5m_pct = float(intraday_technicals.get("move_5m_pct", 0.0))
    consecutive_green = int(intraday_technicals.get("consecutive_green_candles", 0))
    consecutive_red = int(intraday_technicals.get("consecutive_red_candles", 0))
    candle_range_expansion = float(intraday_technicals.get("candle_range_expansion", 1.0))
    efficiency_ratio = float(intraday_technicals.get("efficiency_ratio", 0.5))
    is_choppy = bool(intraday_technicals.get("is_choppy", False))
    trend_state = str(intraday_technicals.get("trend_state", "CHOPPY_SIDEWAYS"))
    rsi_14 = float(intraday_technicals.get("rsi_14", 50.0))

    # -----------------------------------------------------------------------
    # UPGRADE 5 GUARD 1: Hard Sideways Block & Afternoon Chop Guard (Post-14:00 IST)
    # -----------------------------------------------------------------------
    # Never allow directional debit spreads when Trend == CHOPPY_SIDEWAYS
    if trend_state == "CHOPPY_SIDEWAYS":
        logger.info(
            f"[GATEKEEPER] {clean_ticker} trend is CHOPPY_SIDEWAYS. "
            f"Blocking directional entry in choppy/neutral regime."
        )
        return False

    # Block new directional entries after 14:00 IST unless ER >= 0.55 (STRONG_UPTREND or STRONG_DOWNTREND)
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(timezone.utc).astimezone(ist_tz)
    is_after_1400 = (now_ist.hour > 14) or (now_ist.hour == 14 and now_ist.minute >= 0)

    if is_after_1400:
        if efficiency_ratio < 0.55 or trend_state not in ("STRONG_UPTREND", "STRONG_DOWNTREND"):
            logger.info(
                f"[GATEKEEPER] Afternoon Chop Guard: After 14:00 IST ({now_ist.strftime('%H:%M')} IST), "
                f"directional entries require ER >= 0.55 and STRONG trend. Current ER={efficiency_ratio:.2f}, "
                f"Trend={trend_state}. Blocking entry to prevent late-afternoon theta/whipsaw loss."
            )
            return False

    # -----------------------------------------------------------------------
    # STEP 20 GUARD 1: Hard Chop Block (Kaufman Efficiency Ratio < 0.38)
    # -----------------------------------------------------------------------
    if is_choppy or efficiency_ratio < 0.38:
        logger.info(
            f"[GATEKEEPER] {clean_ticker} is in CHOPPY WHIPSAW regime (ER={efficiency_ratio:.2f}). "
            f"Blocking trade to prevent theta/whipsaw loss."
        )
        return False

    # -----------------------------------------------------------------------
    # STEP 20 GUARD 2: Anti-Chasing Block (Overbought / Oversold exhaustion)
    # -----------------------------------------------------------------------
    if rsi_14 > 72.0 and (move_3m_pts > 0 or trend_state == "STRONG_UPTREND"):
        logger.info(
            f"[GATEKEEPER] {clean_ticker} RSI is OVERBOUGHT ({rsi_14:.1f} > 72). "
            f"Blocking top-chasing entry."
        )
        return False

    if rsi_14 < 28.0 and (move_3m_pts < 0 or trend_state == "STRONG_DOWNTREND"):
        logger.info(
            f"[GATEKEEPER] {clean_ticker} RSI is OVERSOLD ({rsi_14:.1f} < 28). "
            f"Blocking bottom-chasing entry."
        )
        return False

    # -----------------------------------------------------------------------
    # STEP 20 GUARD 3: OI Alignment Check for Stock Options
    # -----------------------------------------------------------------------
    if clean_ticker not in INDEX_SYMBOLS:
        if trend_state == "STRONG_UPTREND":
            if not (pcr >= 0.85 or atm_put_oi_change > atm_call_oi_change):
                logger.info(
                    f"[GATEKEEPER] {clean_ticker} UPTREND lacks Put OI support "
                    f"(PCR={pcr:.2f}, PE chg={atm_put_oi_change}, CE chg={atm_call_oi_change}). Blocking trade."
                )
                return False
        elif trend_state == "STRONG_DOWNTREND":
            if not (pcr <= 1.05 or atm_call_oi_change > atm_put_oi_change):
                logger.info(
                    f"[GATEKEEPER] {clean_ticker} DOWNTREND lacks Call OI resistance "
                    f"(PCR={pcr:.2f}, CE chg={atm_call_oi_change}, PE chg={atm_put_oi_change}). Blocking trade."
                )
                return False

    # -----------------------------------------------------------------------
    # STEP 24 GUARD 4: Extreme PCR Regime Protection
    # Never fight extreme institutional positioning (heavy Put or Call writing)
    # -----------------------------------------------------------------------
    pcr_breakout_pts = 85.0 if clean_ticker == "BANKNIFTY" else 35.0
    if pcr >= 1.35 and (trend_state == "STRONG_DOWNTREND" or move_5m_pts < 0):
        if move_5m_pts > -pcr_breakout_pts:
            logger.info(
                f"[GATEKEEPER] {clean_ticker} Bearish entry blocked: Heavy bullish institutional floor "
                f"(PCR={pcr:.2f} >= 1.35) without severe breakdown (5m Move: {move_5m_pts:+.1f} pts > -{pcr_breakout_pts} pts)."
            )
            return False

    if pcr <= 0.70 and (trend_state == "STRONG_UPTREND" or move_5m_pts > 0):
        if move_5m_pts < pcr_breakout_pts:
            logger.info(
                f"[GATEKEEPER] {clean_ticker} Bullish entry blocked: Heavy bearish institutional ceiling "
                f"(PCR={pcr:.2f} <= 0.70) without severe breakout (5m Move: {move_5m_pts:+.1f} pts < +{pcr_breakout_pts} pts)."
            )
            return False

    # Extract ATM strike details for tick snapshot tracking
    strikes = market_data_json.get("strikes", [])
    if strikes:
        atm_strike = min(strikes, key=lambda s: abs(float(s.get("strike", 0.0)) - spot))
        atm_ce_oi = int(atm_strike.get("ce_oi", 0))
        atm_pe_oi = int(atm_strike.get("pe_oi", 0))
        atm_ce_chg = int(atm_strike.get("ce_change_oi", 0))
        atm_pe_chg = int(atm_strike.get("pe_change_oi", 0))
    else:
        atm_ce_oi = 0
        atm_pe_oi = 0
        atm_ce_chg = 0
        atm_pe_chg = 0

    history = PREVIOUS_TICKS.get(clean_ticker, [])

    # Compute intraday momentum deltas against previous snapshots
    if history:
        prev = history[-1]
        spot_delta_pts = round(spot - prev.spot_price, 2)
        pcr_delta = round(pcr - prev.pcr, 3)

        tick_1_pct = (
            ((spot - prev.spot_price) / prev.spot_price * 100.0)
            if prev.spot_price > 0
            else 0.0
        )

        rolling_ref = history[-min(len(history), 5)]
        rolling_5_pct = (
            ((spot - rolling_ref.spot_price) / rolling_ref.spot_price * 100.0)
            if rolling_ref.spot_price > 0
            else 0.0
        )

        spot_delta_pct = round(
            rolling_5_pct if abs(rolling_5_pct) >= abs(tick_1_pct) else tick_1_pct,
            4,
        )

        pe_oi_delta = atm_pe_oi - prev.atm_pe_oi
        ce_oi_delta = atm_ce_oi - prev.atm_ce_oi

        if pe_oi_delta == 0 and ce_oi_delta == 0:
            pe_oi_delta = atm_pe_chg
            ce_oi_delta = atm_ce_chg

        if ce_oi_delta != 0:
            oi_imbalance = round(pe_oi_delta / ce_oi_delta, 2)
        elif pe_oi_delta != 0:
            oi_imbalance = 2.0 if pe_oi_delta > 0 else 0.5
        else:
            oi_imbalance = 1.0
    else:
        spot_delta_pts = 0.0
        spot_delta_pct = 0.0
        pcr_delta = 0.0
        if atm_ce_chg != 0:
            oi_imbalance = round(atm_pe_chg / atm_ce_chg, 2)
        elif atm_pe_chg != 0:
            oi_imbalance = 2.0 if atm_pe_chg > 0 else 0.5
        else:
            oi_imbalance = 1.0

    # Append current snapshot to PREVIOUS_TICKS (keep max 10 items)
    current_snapshot = TickSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        spot_price=spot,
        pcr=pcr,
        atm_ce_oi=atm_ce_oi,
        atm_pe_oi=atm_pe_oi,
    )
    if clean_ticker not in PREVIOUS_TICKS:
        PREVIOUS_TICKS[clean_ticker] = []
    PREVIOUS_TICKS[clean_ticker].append(current_snapshot)
    if len(PREVIOUS_TICKS[clean_ticker]) > 10:
        PREVIOUS_TICKS[clean_ticker] = PREVIOUS_TICKS[clean_ticker][-10:]

    # -----------------------------------------------------------------------
    # Rule 1 — Instant Technical & OI Breakout Trigger (Fast-Path)
    # -----------------------------------------------------------------------
    is_breakout = False

    # STEP 22: Short-Covering / Unwinding Fast-Path Trigger
    if short_covering_signal in ("BULLISH_CALL_UNWINDING", "BEARISH_PUT_UNWINDING") and efficiency_ratio >= 0.42:
        logger.info(
            f"[GATEKEEPER FAST-PATH] Short-covering breakout trigger hit for {clean_ticker}: "
            f"{short_covering_signal} (ER: {efficiency_ratio:.2f}). Bypassing Clef model."
        )
        return True

    if clean_ticker == "NIFTY":
        if (abs(move_3m_pts) >= 15.0 or abs(move_5m_pts) >= 22.0) and efficiency_ratio >= 0.40:
            is_breakout = True
    elif clean_ticker == "BANKNIFTY":
        if (abs(move_3m_pts) >= 35.0 or abs(move_5m_pts) >= 55.0) and efficiency_ratio >= 0.40:
            is_breakout = True
    else:
        # Equities
        if abs(move_5m_pct) >= 0.25 and efficiency_ratio >= 0.40:
            is_breakout = True

    # Any ticker conditions
    if (
        consecutive_green >= 3
        or consecutive_red >= 3
        or candle_range_expansion >= 2.2
        or abs(spot_delta_pct) >= 0.25
    ):
        is_breakout = True

    if is_breakout:
        logger.info(f"[GATEKEEPER] Intraday Breakout Triggered on {clean_ticker}!")
        return True

    # -----------------------------------------------------------------------
    # Rule 2 — Calibrated Clef Evaluation
    # -----------------------------------------------------------------------
    def _deterministic_fallback() -> bool:
        fallback_triggered = (
            (abs(spot_delta_pct) >= 0.18)
            or (abs(move_5m_pct) >= 0.18)
            or (pcr > 1.3)
            or (pcr < 0.7)
            or (consecutive_green >= 2)
            or (consecutive_red >= 2)
        )
        logger.info(
            f"[CLEF FALLBACK] {clean_ticker} | Spot: {spot} (5m: {move_5m_pts:+.1f} pts) | "
            f"Deterministic: {fallback_triggered} (Delta %: {spot_delta_pct:+.2f}%, PCR: {pcr:.2f})"
        )
        return fallback_triggered

    # Construct exact Cloudflare Clef REST payload with real OI structure & 1m technicals
    payload = {
        "model": "clef-flash",
        "state": {
            "ticker": clean_ticker,
            "intraday_deltas": {
                "spot_change_pts": spot_delta_pts,
                "spot_change_pct": spot_delta_pct,
                "pcr_shift": pcr_delta,
                "oi_imbalance": oi_imbalance,
            },
            "intraday_technicals": intraday_technicals,
            "real_oi_structure": {
                "pcr": pcr,
                "volume_pcr": volume_pcr,
                "intraday_oi_pcr": intraday_oi_pcr,
                "short_covering_signal": short_covering_signal,
                "atm_put_oi_change": atm_put_oi_change,
                "atm_call_oi_change": atm_call_oi_change,
                "call_resistance_strike": call_resistance_strike,
                "put_support_strike": put_support_strike,
            },
            "market_snapshot": market_data_json,
        },
        "questions": {
            "actionable": {
                "type": "noul",
                "instructions": (
                    "Given the intraday spot velocity, OI imbalance, PCR shift, "
                    "1-minute candlestick momentum, and options Greeks, is there an "
                    "active intraday momentum breakout, reversal, or IV setup worth analyzing right now?"
                ),
            },
            "momentum_strength": {
                "type": "score",
                "instructions": "Rate the intraday tradable momentum and options flow imbalance.",
                "criteria": ["Dead/Choppy", "Mild Drift", "Clear Intraday Trend", "Explosive Breakout"],
            },
        },
    }

    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", CLOUDFLARE_ACCOUNT_ID).strip()
    api_token = os.getenv("CLOUDFLARE_API_TOKEN", CLOUDFLARE_API_TOKEN).strip()

    if not account_id or not api_token:
        logger.error(
            f"[GATEKEEPER] Cloudflare credentials missing for {clean_ticker}. Falling back to deterministic check."
        )
        return _deterministic_fallback()

    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/cloudflare/clef-flash"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=10)
        if response.status_code != 200:
            logger.error(
                f"[GATEKEEPER] Cloudflare Clef API error HTTP {response.status_code}: {response.text}"
            )
            return _deterministic_fallback()

        data = response.json()
        answers = data.get("result", {}).get("answers", {})
        if not answers or "actionable" not in answers:
            logger.error(
                f"[GATEKEEPER] Cloudflare Clef returned missing or malformed keys: {data}"
            )
            return _deterministic_fallback()

        prob = float(answers.get("actionable", {}).get("noul", 0.0))
        score = float(answers.get("momentum_strength", {}).get("score", 0.0))

        logger.info(
            f"[CLEF SCAN] {clean_ticker} | Spot: {spot} (5m: {move_5m_pts:+.1f} pts) | "
            f"PCR: {pcr:.2f} | Clef Prob: {prob:.2%} | Score: {score:.2f}/3.0"
        )

        # Calibrated Clef threshold: prob >= 0.22 (22%) OR score >= 1.05
        return (prob >= 0.22) or (score >= 1.05)

    except Exception as err:
        logger.error(f"[GATEKEEPER] Cloudflare Clef API request failed: {err}")
        return _deterministic_fallback()


if __name__ == "__main__":
    print("Testing Cloudflare Clef Gatekeeper with Step 20 Anti-Whipsaw Guards...")
    mock_market = {
        "spot_price": 22750.0,
        "pcr": 1.15,
        "volume_pcr": 1.08,
        "call_resistance_strike": 23000,
        "put_support_strike": 22500,
        "atm_call_oi_change": 120000,
        "atm_put_oi_change": 145000,
        "realized_volatility": 0.14,
        "intraday_technicals": {
            "move_3m_pts": 6.5,
            "move_5m_pts": 10.2,
            "move_5m_pct": 0.045,
            "consecutive_green_candles": 2,
            "consecutive_red_candles": 0,
            "candle_range_expansion": 1.2,
            "rsi_14": 56.4,
            "efficiency_ratio": 0.65,
            "is_choppy": False,
            "trend_state": "STRONG_UPTREND",
            "ema_bullish_cross": True,
        },
        "strikes": [
            {"strike": 22600, "ce_ltp": 210.0, "pe_ltp": 60.0, "ce_oi": 80000, "pe_oi": 150000},
            {"strike": 22700, "ce_ltp": 140.0, "pe_ltp": 95.0, "ce_oi": 110000, "pe_oi": 130000},
            {"strike": 22800, "ce_ltp": 85.0, "pe_ltp": 140.0, "ce_oi": 160000, "pe_oi": 90000},
        ],
    }
    result = evaluate_trade_opportunity("NIFTY", mock_market)
    print(f"Clef Gatekeeper Result for NIFTY: {result}")
