"""
Thread-safe Multi-Key Groq Rotator with 65-second Rate Limit Cooldowns
File: backend/groq_pool.py

Implements STEP 21:
- Round-robin Groq API Key Pool (GROQ_API_KEY1, GROQ_API_KEY2, GROQ_API_KEY3, GROQ_API_KEY)
- Automatic 65-second cooldown on 429 RateLimitError with instant failover
- Thread-safe singleton GroqKeyRotator
- Helper function invoke_groq_with_rotation(model_name, temperature, schema_cls, messages)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Type

from dotenv import load_dotenv
from langchain_groq import ChatGroq

# Ensure backend/.env is loaded
dotenv_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path)
load_dotenv()

# Use Uvicorn logger so pool rotation events stream to server logs
logger = logging.getLogger("uvicorn.error")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


class GroqKeyRotator:
    """
    Thread-safe round-robin manager for multiple Groq API keys with automatic
    rate-limit cooldown tracking.
    """
    _instance: Optional[GroqKeyRotator] = None
    _singleton_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> GroqKeyRotator:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self.lock = threading.Lock()
        self.keys: List[Tuple[str, str]] = []
        self.index: int = 0
        self.cooldowns: Dict[str, float] = {}

        seen_keys = set()
        for label in ["GROQ_API_KEY1", "GROQ_API_KEY2", "GROQ_API_KEY3", "GROQ_API_KEY"]:
            val = os.getenv(label, "").strip()
            if val and val not in seen_keys and not val.startswith("your_"):
                seen_keys.add(val)
                self.keys.append((label, val))

        if not self.keys:
            logger.warning("[GROQ POOL] No valid Groq API keys found in environment!")
        else:
            logger.info(f"[GROQ POOL] Initialized pool with {len(self.keys)} Groq API key(s).")

    def get_next_key(self) -> Tuple[str, str]:
        with self.lock:
            if not self.keys:
                raise RuntimeError("No Groq API keys configured in pool.")

            now = time.time()
            total_keys = len(self.keys)

            for _ in range(total_keys):
                label, key = self.keys[self.index]
                self.index = (self.index + 1) % total_keys
                exp = self.cooldowns.get(label, 0.0)
                if now >= exp:
                    return label, key

            # All keys are currently in cooldown: pick the one expiring soonest
            soonest_label, soonest_key = min(
                self.keys, key=lambda k: self.cooldowns.get(k[0], 0.0)
            )
            sleep_time = max(0.1, self.cooldowns.get(soonest_label, 0.0) - now)
            logger.warning(
                f"[GROQ POOL] All {total_keys} Groq keys are in cooldown. "
                f"Waiting {sleep_time:.1f}s for {soonest_label} to recover..."
            )

        time.sleep(sleep_time)
        return soonest_label, soonest_key

    def mark_rate_limited(self, key_label: str, cooldown_seconds: float = 65.0):
        with self.lock:
            self.cooldowns[key_label] = time.time() + cooldown_seconds
            logger.warning(
                f"[GROQ POOL] {key_label} hit 429 Rate Limit. "
                f"Placed on {cooldown_seconds}s cooldown. Rotating to next key..."
            )


def invoke_groq_with_rotation(
    model_name: str = "llama-3.3-70b-versatile",
    temperature: float = 0.2,
    schema_cls: Optional[Type[Any]] = None,
    messages: Optional[List[Any]] = None,
) -> Any:
    """
    Executes a Groq LLM call using round-robin key rotation and instant 429 failover.
    """
    rotator = GroqKeyRotator.get_instance()
    max_attempts = max(4, len(rotator.keys) * 2)
    last_err: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        key_label, api_key = rotator.get_next_key()
        try:
            llm = ChatGroq(
                model=model_name,
                temperature=temperature,
                api_key=api_key,
                max_retries=0,
            )
            target = llm.with_structured_output(schema_cls) if schema_cls else llm
            result = target.invoke(messages or [])
            logger.info(f"[GROQ POOL] Request succeeded using {key_label} (model: {model_name})")
            return result
        except Exception as exc:
            err_str = str(exc).lower()
            is_429 = (
                "429" in err_str
                or "ratelimit" in err_str
                or "rate limit" in err_str
                or "resourceexhausted" in err_str
                or "tokens per day" in err_str
                or "requests per minute" in err_str
            )
            if is_429:
                rotator.mark_rate_limited(key_label, cooldown_seconds=65.0)
                last_err = exc
                continue
            else:
                logger.error(f"[GROQ POOL] Non-429 error using {key_label}: {exc}")
                raise exc

    raise RuntimeError(f"All Groq keys in pool failed after {max_attempts} attempts: {last_err}")
