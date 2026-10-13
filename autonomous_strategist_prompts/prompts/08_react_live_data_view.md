# STEP 8: React Dashboard Live Market Data & Greeks Visualization
CONTEXT: Updating the React frontend (`frontend/src/App.jsx`) to display the live NSE Options Chain data, Greeks table, and multi-leg trade cards received from the SSE stream.

INSTRUCTION:
Execute the following steps autonomously without asking for confirmation:

1. In `frontend/src/App.jsx`, add React state `marketData` (`null` initially) and `ragContext` (`""` initially) alongside `messages` and `finalTrade`.

2. Update the SSE stream parser connected to `/api/stream/{ticker}`:
   - Whenever a chunk updates `market_data`, update `marketData` state.
   - Whenever a chunk updates `rag_context`, update `ragContext` state.
   - Append new entries from `messages` without duplicating existing logs.
   - When `final_trade` is populated, set `finalTrade` state.

3. Enhance the UI layout in `frontend/src/App.jsx` using TailwindCSS:
   - Top Metrics Bar: Display Live Spot Price, Nearest Expiry, Put-Call Ratio (PCR) with a Bullish/Bearish badge, and 5-Day Return % from `marketData`.
   - Center Split View:
     - Top Half: A compact "Live NSE Options Chain & Black-Scholes Greeks" table rendering the 5 nearest strikes (`CE OI`, `CE IV`, `CE Delta`, `CE LTP`, `STRIKE`, `PE LTP`, `PE Delta`, `PE IV`, `PE OI`).
     - Bottom Half: The auto-scrolling live Agent Reasoning Terminal showing each node (`Data Engineer`, `RAG Researcher`, `Quant Analyst`, `Risk Manager`) debating and refining the setup.
   - Right Panel: Render the `finalTrade` card displaying `strategy_name`, `market_bias`, each option leg (`BUY/SELL`, `Strike`, `CE/PE`, `LTP`, `Delta`), `max_profit`, `max_loss`, `stop_loss`, `target`, and the Quant's rationale.