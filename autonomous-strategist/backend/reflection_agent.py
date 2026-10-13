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
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from database import Position
from rag_engine import store_trade_reflection

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
def _get_reflection_llm():
    """Instantiates Groq or Gemini for structured post-mortem generation."""
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key and groq_key != "your_groq_api_key_here":
        try:
            from langchain_groq import ChatGroq
            model_name = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")
            return ChatGroq(model=model_name, temperature=0.2, groq_api_key=groq_key)
        except Exception as e:
            logger.warning(f"Failed loading Groq for reflection: {e}")

    gemini_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if gemini_key and gemini_key != "your_gemini_api_key_here":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(
                model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                temperature=0.2,
                api_key=gemini_key,
            )
        except Exception as e:
            logger.warning(f"Failed loading Gemini for reflection: {e}")

    return None


# ---------------------------------------------------------------------------
# 3. Post-Mortem Generator
# ---------------------------------------------------------------------------
def generate_postmortem(position: Position) -> TradePostMortem:
    """
    Evaluates a closed trade and generates a structured post-mortem critique.
    Saves the rule into ChromaDB trade_reflections.
    """
    ticker = position.ticker.upper()
    strategy = position.strategy_name
    pnl = float(position.pnl or 0.0)
    status = position.status
    thesis = position.thesis
    net_prem = position.net_premium
    exit_price = position.exit_price or 0.0

    outcome = "WIN" if pnl > 0 else "LOSS"
    logger.info(f"Generating post-mortem for [{ticker}] - {strategy} ({outcome}: INR {pnl:,.2f}, Status: {status})...")

    llm = _get_reflection_llm()
    postmortem_obj: Optional[TradePostMortem] = None

    if llm:
        system_prompt = (
            "You are the Lead Quantitative Reviewer & Post-Mortem Strategist at a proprietary options desk. "
            "Your job is to analyze completed trades, diagnose structural flaws or strengths, and derive "
            "a single concise 'rule_to_remember' that will prevent future losses on this asset."
        )

        user_prompt = (
            f"Closed Trade Details:\n"
            f"- Ticker: {ticker}\n"
            f"- Strategy: {strategy}\n"
            f"- Outcome: {outcome} (Realized P&L: INR {pnl:,.2f})\n"
            f"- Exit Status: {status}\n"
            f"- Net Entry Premium: INR {net_prem}\n"
            f"- Final Exit Price: INR {exit_price}\n"
            f"- Original Quant Thesis: {thesis}\n\n"
            "Diagnose the root cause, critique the execution, and formulate the single golden rule_to_remember."
        )

        try:
            structured_llm = llm.with_structured_output(TradePostMortem)
            postmortem_obj = structured_llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
        except Exception as exc:
            logger.warning(f"LLM post-mortem generation failed ({exc}). Using deterministic rules.")

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
                rule_to_remember = f"Avoid entering narrow spreads on {ticker} when spot is within 0.5% of major technical pivots."
            else:
                root_cause = "Options theta decay and gamma risk eroded capital faster than anticipated."
                rule_to_remember = f"Require minimum 2 strike steps width for protective wings on {ticker} weekly expiries."

            postmortem_obj = TradePostMortem(
                root_cause=root_cause,
                execution_critique=f"Closed at stop loss with exit price INR {exit_price}. Risk discipline saved further drawdown.",
                rule_to_remember=rule_to_remember,
            )

    # Persist lesson to ChromaDB
    store_trade_reflection({
        "ticker": ticker,
        "strategy": strategy,
        "pnl": pnl,
        "exit_reason": status,
        "root_cause": postmortem_obj.root_cause,
        "rule_to_remember": postmortem_obj.rule_to_remember,
    })

    print(f"\n=======================================================")
    print(f" TRADE POST-MORTEM & REFLECTION GENERATED")
    print(f" Symbol: {ticker} | Strategy: {strategy} | Result: {outcome} (INR {pnl:,.2f})")
    print(f" Root Cause: {postmortem_obj.root_cause}")
    print(f" Rule To Remember: {postmortem_obj.rule_to_remember}")
    print(f"=======================================================\n")

    return postmortem_obj


def trigger_trade_postmortem(closed_position: Position):
    """Hook called automatically when a position is closed."""
    try:
        generate_postmortem(closed_position)
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
