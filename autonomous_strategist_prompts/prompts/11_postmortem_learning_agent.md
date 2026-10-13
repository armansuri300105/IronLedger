# STEP 11: Agent Post-Mortem & Memory Reflection Loop
CONTEXT: Making the agent learn from mistakes by building a Reflection Agent that stores lessons in ChromaDB and retrieves them during future strategy generation.

INSTRUCTION:
Execute the following steps autonomously without asking for confirmation:

1. In `backend/rag_engine.py`:
   - Create a second persistent ChromaDB collection: `trade_reflections`.
   - Function `store_trade_reflection(reflection_data: dict)`:
     - Formats trade metadata (ticker, strategy, pnl, result: WIN/LOSS, exit_reason).
     - Embeds and stores the diagnostic lesson text with metadata:
       `{"ticker": ticker, "strategy": strategy, "result": "WIN" if pnl > 0 else "LOSS"}`.
   - Function `retrieve_relevant_lessons(ticker: str, strategy_name: str, n_results: int = 3) -> str`:
     - Queries `trade_reflections` using semantic search on `f"{ticker} {strategy_name} mistakes losses invalidation"`.
     - Returns formatted markdown of past mistakes and rules learned to avoid repeated errors.

2. Create `backend/reflection_agent.py`:
   - Define `Pydantic` schema `TradePostMortem`:
     - `root_cause`: str (e.g., "Premature entry before IV crush", "Failed to account for resistance at 24,500", "Theta decay exceeded delta gains")
     - `execution_critique`: str (Analysis of entry/exit timing and strike selection)
     - `rule_to_remember`: str (A single, strict actionable directive for future trades)
   - Implement `generate_postmortem(closed_position) -> TradePostMortem`:
     - Calls `ChatGroq(model="llama-3.3-70b-versatile", temperature=0.2).with_structured_output(TradePostMortem)`.
     - Prompt includes: original `thesis`, strategy name, strikes, entry price, exit price, final P&L, and reason for exit.
     - Saves the generated `rule_to_remember` and post-mortem into ChromaDB via `store_trade_reflection`.
     - Logs the post-mortem to the terminal.

3. Update `backend/graph.py` (Connecting the Learning Loop):
   - In `StrategistState`, add `past_lessons: str`.
   - Before the `quant_analyst` node runs:
     - Query `retrieve_relevant_lessons(state["ticker"], "options strategy")`.
     - Inject `past_lessons` into the Quant Analyst's prompt:
       `"CRITICAL MEMORY - PAST MISTAKES ON THIS ASSET:\n{past_lessons}\nYou MUST verify your proposed trade does NOT repeat any of the above mistakes."`