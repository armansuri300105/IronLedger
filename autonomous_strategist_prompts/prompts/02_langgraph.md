# STEP 2: LangGraph Architecture
CONTEXT: Building the multi-agent state machine.
INSTRUCTION:
1. In `backend/graph.py`, import langgraph and pydantic.
2. Define `StrategistState(TypedDict)` with: ticker (str), messages (list), market_data (dict), rag_context (str), quant_proposal (dict), risk_approved (bool), final_trade (dict).
3. Create 4 placeholder node functions: `data_engineer`, `rag_researcher`, `quant_analyst`, `risk_manager`.
4. For `data_engineer`, use a mock dictionary to simulate fetching NSE options chain data for the state ticker (include dummy Greeks and IV).
5. For `risk_manager`, implement basic deterministic logic that checks if the proposed trade max_loss is within bounds.
6. Construct the StateGraph, add nodes, and define conditional edges: if risk is rejected, route back to `quant_analyst`. If approved, route to END.
