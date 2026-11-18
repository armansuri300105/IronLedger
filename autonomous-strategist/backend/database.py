"""
Paper Trading Database & Virtual Ledger
File: backend/database.py

Implements STEP 9:
- Account model with virtual capital (10,00,000 INR default), margin tracking, and realized P&L
- Position model with multi-leg JSON, capital allocation, stop-loss/target, and exit status
- SQLite persistence via SQLModel at backend/paper_trader.db
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Generator, List, Optional

from dotenv import load_dotenv
from sqlmodel import Field, Session, SQLModel, create_engine, select

dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path)
load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), "paper_trader.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

# Connect args needed for SQLite concurrency
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

# ---------------------------------------------------------------------------
# Daily Trading Caps & Anti-Churn Limits (Central Configuration - UPGRADE 50)
# ---------------------------------------------------------------------------
# Institutional Blueprint Lock: Max 3 trades per symbol per day, max 6 total
MAX_DAILY_TOTAL_TRADES: int = int(os.getenv("MAX_DAILY_TOTAL_TRADES", "6"))
MAX_DAILY_SYMBOL_TRADES: int = int(os.getenv("MAX_DAILY_SYMBOL_TRADES", "3"))


# ---------------------------------------------------------------------------
# 1. Models
# ---------------------------------------------------------------------------
class Account(SQLModel, table=True):
    """Virtual trading account tracking balance, margin usage, and lifetime P&L."""
    id: Optional[int] = Field(default=None, primary_key=True)
    virtual_balance: float = Field(default=1000000.0, description="Available virtual cash in INR")
    margin_used: float = Field(default=0.0, description="Capital locked in open positions")
    realized_pnl: float = Field(default=0.0, description="Cumulative realized P&L across all closed trades")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Position(SQLModel, table=True):
    """Option or equity paper trade position."""
    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    strategy_name: str
    legs_json: str = Field(description="JSON serialized list of legs with fill prices, deltas, qty")
    net_premium: float = Field(description="Net premium received (credit) or paid (debit) per unit")
    capital_allocated: float = Field(description="Total capital/margin committed to this trade")
    stop_loss: float = Field(description="Stop-loss price or threshold")
    target: float = Field(description="Target profit price or threshold")
    entry_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    exit_timestamp: Optional[datetime] = Field(default=None)
    exit_price: Optional[float] = Field(default=None)
    pnl: Optional[float] = Field(default=None, description="Final realized profit/loss in INR (Net PnL)")
    gross_pnl: Optional[float] = Field(default=None, description="Gross PnL before brokerage and charges")
    charges: Optional[float] = Field(default=0.0, description="Total Indian F&O brokerage and statutory taxes")
    total_charges: Optional[float] = Field(default=0.0, description="Total Indian F&O charges (Upgrade 30)")
    net_pnl: Optional[float] = Field(default=None, description="True Net Realized P&L (gross_pnl - total_charges, Upgrade 30)")
    status: str = Field(default="OPEN", index=True)  # OPEN, CLOSED_TARGET, CLOSED_SL, CLOSED_EXPIRY, CLOSED_MANUAL
    thesis: str = Field(default="", description="Quant Analyst original thesis")
    conviction_tier: Optional[str] = Field(default="HIGH_CONVICTION", description="Conviction tier: HIGH_CONVICTION or SCOUT_PROBE (Upgrade 38)")
    entry_spot: Optional[float] = Field(default=None, description="Underlying spot at order fill (Upgrade 41)")


# ---------------------------------------------------------------------------
# 2. Database Initialization & Helpers
# ---------------------------------------------------------------------------
def init_db():
    """Initializes tables, applies column migrations, and seeds default Account if empty."""
    SQLModel.metadata.create_all(engine)
    try:
        with engine.connect() as conn:
            res = conn.exec_driver_sql("PRAGMA table_info(position)")
            cols = [row[1] for row in res.fetchall()]
            if cols and "gross_pnl" not in cols:
                conn.exec_driver_sql("ALTER TABLE position ADD COLUMN gross_pnl FLOAT")
            if cols and "charges" not in cols:
                conn.exec_driver_sql("ALTER TABLE position ADD COLUMN charges FLOAT DEFAULT 0.0")
            if cols and "total_charges" not in cols:
                conn.exec_driver_sql("ALTER TABLE position ADD COLUMN total_charges FLOAT DEFAULT 0.0")
            if cols and "net_pnl" not in cols:
                conn.exec_driver_sql("ALTER TABLE position ADD COLUMN net_pnl FLOAT")
            if cols and "conviction_tier" not in cols:
                conn.exec_driver_sql("ALTER TABLE position ADD COLUMN conviction_tier VARCHAR DEFAULT 'HIGH_CONVICTION'")
            if cols and "entry_spot" not in cols:
                conn.exec_driver_sql("ALTER TABLE position ADD COLUMN entry_spot FLOAT")

            # UPGRADE 30: Backfill missing total_charges, net_pnl, and gross_pnl for existing positions
            conn.exec_driver_sql("""
                UPDATE position
                SET total_charges = COALESCE(charges, 0.0)
                WHERE total_charges IS NULL OR (total_charges = 0.0 AND charges > 0.0)
            """)
            conn.exec_driver_sql("""
                UPDATE position
                SET net_pnl = COALESCE(pnl, ROUND(COALESCE(gross_pnl, 0.0) - COALESCE(total_charges, 0.0), 2))
                WHERE net_pnl IS NULL AND pnl IS NOT NULL
            """)
            conn.exec_driver_sql("""
                UPDATE position
                SET gross_pnl = COALESCE(gross_pnl, pnl)
                WHERE gross_pnl IS NULL AND pnl IS NOT NULL
            """)
            conn.commit()
    except Exception as exc:
        pass

    with Session(engine) as session:
        statement = select(Account).where(Account.id == 1)
        account = session.exec(statement).first()
        if not account:
            default_account = Account(
                id=1,
                virtual_balance=1000000.0,
                margin_used=0.0,
                realized_pnl=0.0,
                created_at=datetime.now(timezone.utc),
            )
            session.add(default_account)
            session.commit()


def count_today_trades(session: Session, ticker: Optional[str] = None) -> int:
    """
    Returns the count of executed trades in the current IST trading session.
    Used for the anti-churn 4-trade daily cap and 2-trade per symbol cap.
    """
    from datetime import timedelta
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(timezone.utc).astimezone(ist_tz)
    today_start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_ist.astimezone(timezone.utc)

    statement = select(Position).where(Position.entry_timestamp >= today_start_utc)
    if ticker:
        statement = statement.where(Position.ticker == ticker.strip().upper())
    records = session.exec(statement).all()
    return len(records)


def get_session() -> Generator[Session, None, None]:
    """Provides a database session for FastAPI dependency injection."""
    with Session(engine) as session:
        yield session


def get_account(session: Session) -> Account:
    """Retrieves or creates the primary virtual account."""
    account = session.exec(select(Account).where(Account.id == 1)).first()
    if not account:
        account = Account(id=1, virtual_balance=1000000.0, margin_used=0.0, realized_pnl=0.0)
        session.add(account)
        session.commit()
        session.refresh(account)
    return account


def reset_paper_trading_db(engine_override=None):
    """
    UPGRADE 45: Factory Reset of Paper Trading Database & Zombie Positions.
    Wipes all rows in the Position table (both OPEN and CLOSED_*) and resets Account to initial state:
    - virtual_balance = 1000000.0 (Free Cash: ₹1,000,000.00)
    - margin_used = 0.0
    - realized_pnl = 0.0
    """
    target_engine = engine_override or engine
    with Session(target_engine) as session:
        positions = session.exec(select(Position)).all()
        for p in positions:
            session.delete(p)

        account = session.exec(select(Account).where(Account.id == 1)).first()
        if not account:
            account = Account(id=1, virtual_balance=1000000.0, margin_used=0.0, realized_pnl=0.0)
            session.add(account)
        else:
            account.virtual_balance = 1000000.0
            account.margin_used = 0.0
            account.realized_pnl = 0.0
            session.add(account)
        session.commit()

    try:
        import position_monitor
        position_monitor.ENTRY_SPOT_CACHE.clear()
        position_monitor.POSITION_TELEMETRY.clear()
    except Exception:
        pass

    try:
        import autopilot
        autopilot.AUTOPILOT_STATE["exit_logs"] = []
    except Exception:
        pass

    # Ensure paper_trading.db replica is also cleanly synchronized if disk engine was reset
    if target_engine == engine and os.path.exists(DB_PATH):
        try:
            import shutil
            alt_path = os.path.join(os.path.dirname(__file__), "paper_trading.db")
            shutil.copyfile(DB_PATH, alt_path)
        except Exception:
            pass


# Initialize tables and seed default account on module load
init_db()


if __name__ == "__main__":
    with Session(engine) as s:
        acc = get_account(s)
        print(f"Database initialized at {DB_PATH}")
        print(f"Account Balance: INR {acc.virtual_balance:,.2f} | Margin Used: INR {acc.margin_used:,.2f}")
