import json
from pathlib import Path
from typing import List, Optional

import duckdb
import pandas as pd

from .event import Event

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def get_connection(db_path: str = "data/llens.db") -> duckdb.DuckDBPyConnection:
    if db_path != ":memory:":
        db_path = str(Path(db_path))
    con = duckdb.connect(db_path)
    con.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    return con


def insert_batch(
    con: duckdb.DuckDBPyConnection,
    *,
    file_name: str,
    source_system: Optional[str] = None,
    profile_name: Optional[str] = None,
    structure: Optional[str] = None,
    match_ratio: Optional[float] = None,
    row_count: Optional[int] = None,
) -> int:
    con.execute(
        """
        INSERT INTO batches (file_name, source_system, profile_name, structure, match_ratio, row_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [file_name, source_system, profile_name, structure, match_ratio, row_count],
    )
    return con.execute("SELECT currval('batch_seq')").fetchone()[0]


def insert_events(con: duckdb.DuckDBPyConnection, events: List[Event]) -> None:
    if not events:
        return
    rows = [
        (
            e.batch_id,
            e.line_no,
            e.ts_utc,
            e.ts_raw,
            e.level,
            e.category,
            e.component,
            e.message,
            e.raw,
            json.dumps(e.attributes) if e.attributes else None,
        )
        for e in events
    ]
    con.executemany(
        """
        INSERT INTO events (batch_id, line_no, ts_utc, ts_raw, level, category,
                            component, message, raw, attributes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def events_to_df(con: duckdb.DuckDBPyConnection, batch_id: int) -> pd.DataFrame:
    return con.execute(
        "SELECT * FROM events WHERE batch_id = ? ORDER BY ts_utc, line_no",
        [batch_id],
    ).fetchdf()


def batches_to_df(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("SELECT * FROM batches ORDER BY ingested_at DESC").fetchdf()
