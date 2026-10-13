# STEP 3: FastAPI Backend & SSE
CONTEXT: Serving the LangGraph via real-time streaming so the frontend can see the agent's thought process.
INSTRUCTION:
1. In `backend/main.py`, initialize FastAPI with CORS enabled.
2. Create a GET endpoint `/api/stream/{ticker}` that uses FastAPI's `StreamingResponse` (Server-Sent Events).
3. The stream should execute the compiled LangGraph using `graph.stream()` and yield JSON string chunks as the agent nodes update the state.
4. Ensure the yielded format matches standard SSE (`data: {json_chunk}

`).
