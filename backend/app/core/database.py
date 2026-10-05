"""SQLite connection. Money is stored as integer tiyn (1 KZT = 100 tiyn)."""
import os
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_PATH = Path(__file__).resolve().parents[3] / "aman_bank.db"
DATABASE_URL = os.getenv("AMAN_DATABASE_URL", f"sqlite:///{DB_PATH.as_posix()}")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 15})

@event.listens_for(engine, "connect")
def configure_sqlite(connection, _):
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=15000")

class Base(DeclarativeBase):
    pass

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

def get_db():
    with SessionLocal() as session:
        yield session
