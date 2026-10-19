"""
Backward-compatibility alias pointing to Cloudflare Clef Gatekeeper.
File: backend/jev_gatekeeper.py
"""

from clef_gatekeeper import evaluate_trade_opportunity

__all__ = ["evaluate_trade_opportunity"]
