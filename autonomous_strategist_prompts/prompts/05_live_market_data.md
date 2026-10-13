# STEP 5: Live NSE Options Chain & Real Greeks Calculation
CONTEXT: Upgrading the `data_engineer` node from mock data to real-time NSE options chain data, live Open Interest (OI), Put-Call Ratio (PCR), and Black-Scholes Greeks.

INSTRUCTION:
Execute the following steps autonomously without asking for confirmation:

1. In the `backend` virtual environment, install additional data & math dependencies:
   `pip install yfinance scipy python-dotenv requests` and update `backend/requirements.txt`.

2. Create `backend/market_data.py` with a function `fetch_live_options_data(ticker: str) -> dict`:
   - Clean and uppercase `ticker` (e.g., "NIFTY", "BANKNIFTY", "RELIANCE").
   - Use `nsepython`'s `nse_optionchain_scrapper(ticker)` to fetch the live NSE option chain JSON.
   - Implement a resilient fallback: if NSE blocks or returns empty outside market hours, use a custom `requests.Session()` with browser `User-Agent` headers hitting `https://www.nseindia.com/api/option-chain-indices?symbol={ticker}` (for NIFTY/BANKNIFTY/FINNIFTY) or `https://www.nseindia.com/api/option-chain-equities?symbol={ticker}` after priming cookies on `https://www.nseindia.com`.
   - Extract from `records`:
     - `underlyingValue` (Spot Price)
     - Nearest expiry date from `records['expiryDates'][0]`
     - Total Call OI and Total Put OI across the nearest expiry to calculate exact `pcr` (Put-Call Ratio = Total Put OI / Total Call OI).
   - Filter the option chain for the nearest expiry and select the 5 strikes closest to `underlyingValue` (ATM, 2 ITM, 2 OTM).
   - Write a helper function `calculate_greeks(spot, strike, time_to_expiry_years, iv, rate=0.07, option_type="CE")` using `scipy.stats.norm` and standard Black-Scholes formulas to compute exact `delta`, `gamma`, `theta` (per day), and `vega` (per 1% IV move) from the live `impliedVolatility` field returned by NSE.
   - Fetch the last 14 days of daily OHLCV data using `yfinance` (`^NSEI` for NIFTY, `^NSEBANK` for BANKNIFTY, or `{ticker}.NS` for equities) and compute 5-day price return (%) and 14-day realized volatility.
   - Return a structured dictionary containing: `spot_price`, `nearest_expiry`, `pcr`, `recent_5d_return_pct`, `realized_volatility`, and `strikes` (list of the 5 nearest strikes with CE and PE LTP, OI, Change in OI, IV, Delta, Gamma, Theta, Vega).

3. Update `data_engineer(state: StrategistState)` in `backend/graph.py` to call `fetch_live_options_data(state["ticker"])`, populate `state["market_data"]`, and append a detailed log string to `state["messages"]` summarizing the live Spot Price, PCR, ATM IV, and nearest expiry.