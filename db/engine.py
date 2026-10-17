"""SQLite engine setup: WAL mode + busy_timeout for concurrent access."""
import os
import threading

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///kirana_store.db")

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})

# The agent SDK can dispatch several tool calls from one turn onto separate threads
# (e.g. modify_khata_credit + get_khata_balance called "in parallel"). SQLite WAL
# readers see a snapshot as of when their transaction started, so a read racing a
# same-turn write can miss it. A single global lock around every DB session
# serializes all tool DB access and closes that race — at this store's scale
# (one shopkeeper, one bot) that's a fine trade for correctness over throughput.
_DB_LOCK = threading.Lock()


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _migrate_add_missing_columns() -> None:
    """SQLModel.metadata.create_all only creates missing *tables*, not columns on
    tables that already exist — so a field added to a model after first deploy
    (e.g. Sale.payment_reference) needs an explicit ALTER TABLE on existing DBs.
    """
    with engine.connect() as conn:
        for table_name, table in SQLModel.metadata.tables.items():
            existing_cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table_name})")}
            for column in table.columns:
                if column.name not in existing_cols:
                    col_type = column.type.compile(engine.dialect)
                    conn.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column.name} {col_type}")
        conn.commit()


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _migrate_add_missing_columns()


class _LockedSession:
    """Context manager: holds _DB_LOCK for the lifetime of a Session."""

    def __enter__(self) -> Session:
        _DB_LOCK.acquire()
        self._session = Session(engine)
        return self._session

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._session.close()
        finally:
            _DB_LOCK.release()


def get_session() -> _LockedSession:
    return _LockedSession()
