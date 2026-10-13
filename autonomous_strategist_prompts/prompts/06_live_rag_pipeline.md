# STEP 6: Real ChromaDB Vector Store & Live News RAG
CONTEXT: Upgrading the `rag_researcher` node from a placeholder to a real ChromaDB RAG pipeline that indexes live ticker news and local financial documents.

INSTRUCTION:
Execute the following steps autonomously without asking for confirmation:

1. In the `backend` virtual environment, install:
   `pip install beautifulsoup4 lxml pypdf sentence-transformers` and update `backend/requirements.txt`.

2. Create a directory `backend/docs` where users can optionally drop IPO Red Herring Prospectus (RHP) PDFs or company filings.

3. Create `backend/rag_engine.py`:
   - Initialize a persistent ChromaDB client (`chromadb.PersistentClient(path="./chroma_db")`) with a collection named `financial_intel`.
   - Create a function `fetch_and_index_live_news(ticker: str)`:
     - Fetch live Google News RSS for the query `f"{ticker} NSE stock options OR earnings OR IPO India"` via `https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en`.
     - Parse the top 10 news items (title, pubDate, source) using `BeautifulSoup` (`xml` parser).
     - Also scan `backend/docs/` for any `.pdf` or `.txt` files matching the ticker or general market docs, extract text via `pypdf`, and chunk into 800-character segments.
     - Upsert the news headlines and document chunks into the `financial_intel` ChromaDB collection with metadata `{"ticker": ticker.upper()}`.
   - Create a function `query_rag_context(ticker: str, query: str, n_results: int = 5) -> str`:
     - Run `fetch_and_index_live_news(ticker)` first to ensure fresh headlines exist.
     - Query the ChromaDB collection filtered by `{"ticker": ticker.upper()}` and return the top `n_results` documents formatted as a clean markdown string with timestamps and sources.

4. Update `rag_researcher(state: StrategistState)` in `backend/graph.py` to call `query_rag_context(state["ticker"], f"{state['ticker']} price action catalyst volatility earnings")` and store the result in `state["rag_context"]`, appending a summary log to `state["messages"]`.