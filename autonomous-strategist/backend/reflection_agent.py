"""
Trade Reflection & Post-Mortem Learning Agent
File: backend/reflection_agent.py

Implements STEP 11:
- Analyzes closed trades (WIN or LOSS) to extract root causes and strategic flaws
- Generates structured TradePostMortem via ChatGroq / Gemini
- Embeds actionable rules into ChromaDB trade_reflections collection
- Feeds lessons back into the Quant Analyst prompt to eliminate repetitive mistakes
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from database import Position
from rag_engine import store_trade_reflection
from groq_pool import invoke_groq_with_rotation

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reflection_agent")


# ---------------------------------------------------------------------------
# 1. Pydantic Schema for Structured Post-Mortem
# ---------------------------------------------------------------------------
class TradePostMortem(BaseModel):
    """Structured analytical critique generated upon trade closure."""
    root_cause: str = Field(
        description="Core quantitative or technical reason for the trade outcome (e.g., Theta decay, IV crush, breakout failure)"
    )
    execution_critique: str = Field(
        description="Detailed review of entry timing, strike selection, delta balance, and exit discipline"
    )
    rule_to_remember: str = Field(
        description="A single, strict actionable directive that future agent prompts MUST follow to avoid repeating errors"
    )


# ---------------------------------------------------------------------------
# 2. LLM Client Helper
# ---------------------------------------------------------------------------
def _get_reflection_llm_candidates():
    """Returns candidate LLM clients in priority order: Groq (fast LPUs) -> Gemini."""
    clients = []

    # 1. Groq (Fast LPU reasoning model)
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key and groq_key != "your_groq_api_key_here":
        try:
            from langchain_groq import ChatGroq
            model_name = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
            clients.append(ChatGroq(model=model_name, temperature=0.2, groq_api_key=groq_key))
        except Exception as e:
            logger.warning(f"Failed loading Groq for reflection: {e}")

    # 2. Google Gemini fallback
    gemini_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if gemini_key and gemini_key != "your_gemini_api_key_here":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            clients.append(ChatGoogleGenerativeAI(
                model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                temperature=0.2,
                api_key=gemini_key,
            ))
        except Exception as e:
            logger.warning(f"Failed loading Gemini for reflection: {e}")

    return clients


def _get_reflection_llm():
    """Instantiates the first available LLM client for reflection."""
    candidates = _get_reflection_llm_candidates()
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# 3. Post-Mortem Generator
# ---------------------------------------------------------------------------
def generate_postmortem(position: Position, trend_state: Optional[str] = None) -> TradePostMortem:
    """
    Evaluates a closed trade and generates a structured post-mortem critique.
    STEP 22: Enforces clean memory retention by persisting restrictive rules
    to ChromaDB trade_reflections ONLY when pnl < 0 and status in
    ("CLOSED_SL", "SL_HIT"), tagged with trend_state.
    """
    ticker = position.ticker.upper()
    strategy = position.strategy_name
    pnl = float(position.pnl or 0.0)
    status = position.status
    thesis = position.thesis
    net_prem = position.net_premium
    exit_price = position.exit_price or 0.0

    if not trend_state:
        try:
            from position_monitor import POSITION_TELEMETRY
            if position.id is not None:
                trend_state = POSITION_TELEMETRY.get(position.id, {}).get("trend_state")
        except Exception:
            pass
    regime = (trend_state or "CHOPPY_SIDEWAYS").upper()

    outcome = "WIN" if pnl > 0 else "LOSS"
    logger.info(f"Generating post-mortem for [{ticker}] - {strategy} ({outcome}: INR {pnl:,.2f}, Status: {status}, Regime: {regime})...")

    postmortem_obj: Optional[TradePostMortem] = None

    system_prompt = (
        "You are the Lead Quantitative Reviewer & Post-Mortem Strategist at a proprietary options desk. "
        "Your job is to analyze completed trades, diagnose structural flaws or strengths, and derive "
        "a single concise 'rule_to_remember' that will prevent future losses on this asset in the specified market regime.\n\n"
        "CRITICAL: You MUST respond ONLY with a valid JSON object containing the exact keys:\n"
        "- 'root_cause': string\n"
        "- 'execution_critique': string\n"
        "- 'rule_to_remember': string\n"
        "Do NOT call any tools. Output valid JSON object only."
    )

    user_prompt = (
        f"Closed Trade Details:\n"
        f"- Ticker: {ticker}\n"
        f"- Strategy: {strategy}\n"
        f"- Outcome: {outcome} (Realized P&L: INR {pnl:,.2f})\n"
        f"- Exit Status: {status}\n"
        f"- Market Regime at Exit: {regime}\n"
        f"- Net Entry Premium: INR {net_prem}\n"
        f"- Final Exit Price: INR {exit_price}\n"
        f"- Original Quant Thesis: {thesis}\n\n"
        "Diagnose the root cause, critique the execution, and formulate the single golden rule_to_remember as a JSON object."
    )

    try:
        model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        # UPGRADE 36: Prompt-enforced JSON mode without tool-calling to prevent Groq 400 tool_use_failed errors
        raw_resp = invoke_groq_with_rotation(
            model_name=model_name,
            temperature=0.2,
            schema_cls=None,
            messages=[
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ],
            model_kwargs={"response_format": {"type": "json_object"}},
        )
        content_str = raw_resp.content if hasattr(raw_resp, "content") else str(raw_resp)
        if isinstance(content_str, str) and content_str.strip():
            clean_str = content_str.strip()
            if clean_str.startswith("```"):
                clean_str = re.sub(r"^```(?:json)?\s*", "", clean_str)
                clean_str = re.sub(r"\s*```$", "", clean_str)
            parsed_json = json.loads(clean_str)
            postmortem_obj = TradePostMortem(
                root_cause=str(parsed_json.get("root_cause", "")),
                execution_critique=str(parsed_json.get("execution_critique", "")),
                rule_to_remember=str(parsed_json.get("rule_to_remember", "")),
            )
            logger.info("[GROQ POOL] Structured post-mortem successfully generated via prompt-enforced JSON mode.")
    except Exception as exc:
        logger.warning(f"Groq rotator post-mortem generation failed ({exc}). Trying deterministic fallback...")

    # High-precision deterministic fallback
    if not postmortem_obj:
        if pnl >= 0:
            postmortem_obj = TradePostMortem(
                root_cause="Directional and delta-neutral balance aligned with ongoing implied volatility contraction.",
                execution_critique=f"Entry at net premium INR {net_prem} respected target threshold. Target exit achieved cleanly.",
                rule_to_remember=f"Maintain defined risk spreads on {ticker} when Put-Call Ratio confirms momentum.",
            )
        else:
            if "SL" in status:
                root_cause = "Adverse underlying delta move breached stop-loss threshold before theta decay could accumulate."
                rule_to_remember = f"Avoid entering narrow spreads on {ticker} in {regime} when spot is within 0.5% of major technical pivots."
            elif "TREND_REVERSAL" in status:
                root_cause = "1-minute completed candle crossed EMA 20 with adverse spot velocity, triggering proactive capital preservation."
                rule_to_remember = f"Do not fight trend breakdown against EMA 20 on {ticker} in {regime}."
            else:
                root_cause = "Options theta decay and gamma risk eroded capital faster than anticipated."
                rule_to_remember = f"Require minimum 2 strike steps width for protective wings on {ticker} weekly expiries."

            postmortem_obj = TradePostMortem(
                root_cause=root_cause,
                execution_critique=f"Closed at {status} with exit price INR {exit_price}. Risk discipline saved further drawdown.",
                rule_to_remember=rule_to_remember,
            )

    # UPGRADE 32: Balanced Reinforcement Learning in ChromaDB
    # Extract Strike Delta structure, PCR, and ER for balanced learning
    legs = []
    try:
        legs = json.loads(position.legs_json or "[]")
    except Exception:
        pass

    long_delta = 0.48
    short_delta = 0.24
    width = 150 if ticker == "NIFTY" else 400
    for l in legs:
        act = str(l.get("action", "")).upper()
        if act == "BUY":
            long_delta = abs(float(l.get("delta", 0.48) or 0.48))
        elif act == "SELL":
            short_delta = abs(float(l.get("delta", 0.24) or 0.24))
    if len(legs) >= 2:
        try:
            width = abs(int(legs[0].get("strike", 0)) - int(legs[1].get("strike", 0)))
        except Exception:
            pass

    import re
    er_val = 0.85
    pcr_val = 1.15
    if thesis:
        er_match = re.search(r"ER[:=\s]+([0-9\.]+)", thesis, re.IGNORECASE)
        if er_match:
            try:
                er_val = float(er_match.group(1))
            except Exception:
                pass
        pcr_match = re.search(r"PCR[:=\s]+([0-9\.]+)", thesis, re.IGNORECASE)
        if pcr_match:
            try:
                pcr_val = float(pcr_match.group(1))
            except Exception:
                pass

    is_target_win = ("TARGET" in str(status).upper()) or (pnl > 0 and status in ("CLOSED_TARGET", "TARGET_HIT"))
    is_loss_exit = (pnl < 0) and (abs(pnl) >= 150.0) and (status in ("CLOSED_SL", "SL_HIT"))

    if is_target_win:
        blueprint_rule = (
            f"[WINNING BLUEPRINT] {ticker} {strategy} under {regime} (PCR: {pcr_val:.2f}, ER: {er_val:.2f}). "
            f"Structure: Long Delta {long_delta:.2f}, Short Delta {short_delta:.2f}, Width {width} pts. "
            f"Realized Profit: INR {pnl:,.2f}. Replicate this exact strike width and delta calibration in future {regime} breakout cycles."
        )
        postmortem_obj.rule_to_remember = blueprint_rule
        store_trade_reflection({
            "ticker": ticker,
            "strategy": strategy,
            "pnl": pnl,
            "exit_reason": status,
            "root_cause": "Optimal spread delta structure and Kaufman momentum synchronization with trend continuation.",
            "rule_to_remember": blueprint_rule,
            "trend_state": regime,
            "result_type": "WIN",
        })
        logger.info(f"Persisted [WINNING BLUEPRINT] for [{ticker}] under [{regime}] into ChromaDB.")
    elif is_loss_exit:
        # Avoid blanket-banning the entire regime! Tag exact entry flaw.
        loss_flaw_rule = (
            f"Specific Entry Flaw on {ticker} {strategy}: Avoid entering during low ER (< 0.40) or directly into major OI resistance walls. "
            f"Do NOT ban the entire {regime} regime; confirm EMA 9/20 separation and tick velocity before re-entry."
        )
        postmortem_obj.rule_to_remember = loss_flaw_rule
        store_trade_reflection({
            "ticker": ticker,
            "strategy": strategy,
            "pnl": pnl,
            "exit_reason": status,
            "root_cause": postmortem_obj.root_cause,
            "rule_to_remember": loss_flaw_rule,
            "trend_state": regime,
            "result_type": "LOSS",
        })
        logger.info(f"Persisted targeted entry flaw lesson for [{ticker}] under [{regime}] into ChromaDB.")
    else:
        logger.info(
            f"Skipping ChromaDB rule persistence for #{position.id} ({status}, PnL: INR {pnl:,.2f}) "
            f"to prevent memory pollution."
        )

    try:
        root_cause_safe = postmortem_obj.root_cause.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8")
        rule_safe = postmortem_obj.rule_to_remember.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8")
        print(f"\n=======================================================")
        print(f" TRADE POST-MORTEM & REFLECTION GENERATED")
        print(f" Symbol: {ticker} | Strategy: {strategy} | Result: {outcome} (INR {pnl:,.2f})")
        print(f" Regime: {regime} | Status: {status}")
        print(f" Root Cause: {root_cause_safe}")
        print(f" Rule To Remember: {rule_safe}")
        print(f"=======================================================\n")
    except Exception:
        logger.info(f"Root Cause: {postmortem_obj.root_cause}")
        logger.info(f"Rule To Remember: {postmortem_obj.rule_to_remember}")

    return postmortem_obj


def trigger_trade_postmortem(closed_position: Position, trend_state: Optional[str] = None):
    """Hook called automatically when a position is closed."""
    try:
        generate_postmortem(closed_position, trend_state=trend_state)
    except Exception as exc:
        logger.error(f"Error in trigger_trade_postmortem: {exc}", exc_info=True)


if __name__ == "__main__":
    dummy_pos = Position(
        id=99,
        ticker="NIFTY",
        strategy_name="Bull Call Spread",
        legs_json="[]",
        net_premium=14.5,
        capital_allocated=725.0,
        stop_loss=400.0,
        target=1200.0,
        status="CLOSED_SL",
        pnl=-450.0,
        exit_price=5.5,
        thesis="Bullish breakout expected above 22,400",
    )
    pm = generate_postmortem(dummy_pos)
