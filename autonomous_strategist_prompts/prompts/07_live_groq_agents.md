# STEP 7: Live ChatGroq LLM Agents & Structured Risk Loop
CONTEXT: Replacing placeholder logic in `quant_analyst` and `risk_manager` with real Groq LLM reasoning, Pydantic structured outputs, and a bounded conditional feedback loop.

INSTRUCTION:
Execute the following steps autonomously without asking for confirmation:

1. Create `backend/.env.example` with `GROQ_API_KEY=your_groq_api_key_here` and load `.env` in `backend/graph.py` and `backend/main.py` using `from dotenv import load_dotenv; load_dotenv()`.

2. In `backend/graph.py`, update `StrategistState(TypedDict)` to include `revision_count: int` and `risk_feedback: str` alongside the existing fields (`ticker`, `messages`, `market_data`, `rag_context`, `quant_proposal`, `risk_approved`, `final_trade`).

3. Define two Pydantic models in `backend/graph.py`:
   - `QuantProposal(BaseModel)`:
     - `strategy_name`: str (e.g., "Bull Call Spread", "Iron Condor", "Long Straddle")
     - `market_bias`: str ("Bullish", "Bearish", "Neutral", "Volatile")
     - `legs`: list[dict] (Each leg containing `action` BUY/SELL, `option_type` CE/PE, `strike` int, `expiry` str, `ltp` float, `delta` float)
     - `entry_price_net`: float
     - `max_profit`: float
     - `max_loss`: float
     - `stop_loss`: float
     - `target`: float
     - `rationale`: str
   - `RiskEvaluation(BaseModel)`:
     - `approved`: bool
     - `risk_score`: int (1 to 10, where 10 is highest risk)
     - `critique`: str (Detailed feedback if rejected, or approval summary if approved)

4. Implement `quant_analyst(state: StrategistState)` using `ChatGroq(model="llama-3.3-70b-versatile", temperature=0.2).with_structured_output(QuantProposal)`:
   - Pass the live `state["market_data"]`, `state["rag_context"]`, and any prior `state.get("risk_feedback", "")` into the system/human prompt.
   - Instruct the Quant Analyst to select exact strike prices and premiums from `state["market_data"]["strikes"]` and adjust the strategy if `risk_feedback` is present.
   - Save the resulting dict to `state["quant_proposal"]`, increment `revision_count`, and append the Quant's rationale to `state["messages"]`.

5. Implement `risk_manager(state: StrategistState)`:
   - First run deterministic hard rules on `state["quant_proposal"]`:
     - Verify `max_loss <= 15000` (INR per lot equivalent) and Reward-to-Risk ratio (`max_profit / max_loss >= 1.2` when finite).
     - Verify the selected strikes exist in `state["market_data"]["strikes"]` and have non-zero Open Interest.
   - Next, call `ChatGroq(model="llama-3.3-70b-versatile", temperature=0.1).with_structured_output(RiskEvaluation)` to evaluate Greeks exposure (net Delta/Theta vs IV regime and RAG news catalysts).
   - Combine deterministic and LLM checks: if both pass (or if `revision_count >= 3`, forcing a best-effort conclusion with a warning), set `risk_approved = True` and copy the full trade setup + risk metrics into `state["final_trade"]`. Otherwise, set `risk_approved = False`, write the rejection reason into `state["risk_feedback"]`, and append the critique to `state["messages"]`.

6. Ensure the conditional edge function on `risk_manager` routes to `END` if `state["risk_approved"] == True` or `state.get("revision_count", 0) >= 3`, and routes back to `quant_analyst` otherwise.