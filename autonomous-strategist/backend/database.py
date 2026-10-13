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

from sqlmodel import Field, Session, SQLModel, create_engine, select

DB_PATH = os.path.join(os.path.dirname(__file__), "paper_trader.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

# Connect args needed for SQLite concurrency
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)


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
    pnl: Optional[float] = Field(default=None, description="Final realized profit/loss in INR")
    status: str = Field(default="OPEN", index=True)  # OPEN, CLOSED_TARGET, CLOSED_SL, CLOSED_EXPIRY, CLOSED_MANUAL
    thesis: str = Field(default="", description="Quant Analyst original thesis")


# ---------------------------------------------------------------------------
# 2. Database Initialization
# ---------------------------------------------------------------------------
def init_db():
    """Initializes tables and seeds default Account if empty."""
    SQLModel.metadata.create_all(engine)
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


# Initialize tables and seed default account on module load
init_db()


if __name__ == "__main__":
    with Session(engine) as s:
        acc = get_account(s)
        print(f"Database initialized at {DB_PATH}")
        print(f"Account Balance: INR {acc.virtual_balance:,.2f} | Margin Used: INR {acc.margin_used:,.2f}")
