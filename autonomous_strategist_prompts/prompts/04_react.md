# STEP 4: React Dashboard
CONTEXT: Building the frontend interface to visualize agent thoughts and market data.
INSTRUCTION:
1. In `frontend/src/App.jsx`, create a layout with a Sidebar for inputs, a Main section for logs, and a Right Panel for the final trade JSON.
2. Build a function that uses the native `EventSource` API or `fetch` to connect to the backend `/api/stream/{ticker}` endpoint.
3. Parse the SSE chunks and append the agent thoughts/messages to a `messages` array in the React state.
4. Auto-scroll the terminal view as new agent logs come in.
5. When the final `risk_approved = true` state arrives in the chunk, render the `final_trade` object into a stylized Tailwind card.
