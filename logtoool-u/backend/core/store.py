"""
SQLite Storage Engine with SQLAlchemy ORM and WAL mode enabled.
Provides high-performance batch insertion, server-side pagination, and SQL filtering.
"""

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple
import logging
from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.core.schema import BatchRecord, CanonicalLogEvent, LogLevel, TimestampConfidence

logger = logging.getLogger("logtool.store")

Base = declarative_base()


class BatchModel(Base):
    __tablename__ = "batches"

    batch_id = Column(String, primary_key=True)
    file_name = Column(String, nullable=False)
    file_size_bytes = Column(Integer, nullable=False)
    total_events = Column(Integer, default=0)
    matched_profile = Column(String, nullable=True)
    matched_profile_version = Column(String, nullable=True)
    match_ratio = Column(Float, default=0.0)
    uploaded_at = Column(String, nullable=False)


class EventModel(Base):
    __tablename__ = "events"

    event_id = Column(String, primary_key=True)
    batch_id = Column(String, ForeignKey("batches.batch_id", ondelete="CASCADE"), nullable=False)
    file_name = Column(String, nullable=False)
    line_no = Column(Integer, nullable=False)
    ts_utc = Column(String, nullable=True)
    ts_raw = Column(String, nullable=False)
    ts_confidence = Column(String, nullable=False)
    level = Column(String, nullable=False)
    source_system = Column(String, nullable=False)
    component = Column(String, nullable=True)
    message = Column(Text, nullable=False)
    raw = Column(Text, nullable=False)
    attributes = Column(Text, nullable=True)  # JSON string

    __table_args__ = (
        Index("idx_events_ts_utc", "ts_utc"),
        Index("idx_events_level", "level"),
        Index("idx_events_source", "source_system"),
        Index("idx_events_component", "component"),
        Index("idx_events_batch_id", "batch_id"),
        Index("idx_events_file_name", "file_name"),
        Index("idx_events_source_ts", "source_system", "ts_utc"),
    )


class DatabaseManager:
    """
    Manages SQLite database connections, schema setup, WAL mode, batch inserts, and analytical queries.
    """
    def __init__(self, db_path: str = "data/logs.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False, "timeout": 30.0},
            echo=False
        )
        self.Session = sessionmaker(bind=self.engine)
        self.init_db()

    def init_db(self) -> None:
        """Initializes tables and WAL mode."""
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode = WAL;"))
            conn.execute(text("PRAGMA synchronous = NORMAL;"))
            conn.execute(text("PRAGMA foreign_keys = ON;"))
            conn.commit()
        Base.metadata.create_all(self.engine)
        self._ensure_composite_indexes()
        logger.info(f"Database initialized at {self.db_path} with WAL mode enabled.")

    def _ensure_composite_indexes(self) -> None:
        """create_all() only creates missing TABLES, not missing indexes on
        existing tables.  This adds any new composite indexes that were
        introduced after the initial schema."""
        with self.engine.connect() as conn:
            existing = {row[1] for row in conn.execute(text("PRAGMA index_list(events)")).fetchall()}
            if "idx_events_source_ts" not in existing:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_events_source_ts ON events(source_system, ts_utc)"))
                conn.commit()
                logger.info("Created composite index idx_events_source_ts on events(source_system, ts_utc).")

            batch_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(batches)")).fetchall()}
            if "matched_profile_version" not in batch_cols:
                conn.execute(text("ALTER TABLE batches ADD COLUMN matched_profile_version VARCHAR"))
                conn.commit()
                logger.info("Migrated batches table: added matched_profile_version column.")

    def insert_batch_and_events(
        self,
        batch: BatchRecord,
        events: List[CanonicalLogEvent]
    ) -> None:
        """
        Inserts a batch record and all its parsed events in a single transaction.
        Uses high-performance bulk dictionary insertion.
        """
        session = self.Session()
        try:
            batch_obj = BatchModel(
                batch_id=batch.batch_id,
                file_name=batch.file_name,
                file_size_bytes=batch.file_size_bytes,
                total_events=batch.total_events,
                matched_profile=batch.matched_profile,
                matched_profile_version=batch.matched_profile_version,
                match_ratio=batch.match_ratio,
                uploaded_at=batch.uploaded_at
            )
            session.add(batch_obj)
            session.flush()

            event_dicts = [
                {
                    "event_id": e.event_id,
                    "batch_id": e.batch_id,
                    "file_name": e.file_name,
                    "line_no": e.line_no,
                    "ts_utc": e.ts_utc,
                    "ts_raw": e.ts_raw,
                    "ts_confidence": e.ts_confidence.value,
                    "level": e.level.value,
                    "source_system": e.source_system,
                    "component": e.component,
                    "message": e.message,
                    "raw": e.raw,
                    "attributes": json.dumps(e.attributes) if e.attributes else "{}"
                }
                for e in events
            ]

            if event_dicts:
                session.bulk_insert_mappings(EventModel, event_dicts)

            session.commit()
            logger.info(f"Successfully committed batch {batch.batch_id} with {len(events)} events.")
        except Exception as e:
            session.rollback()
            logger.error(f"Failed inserting batch {batch.batch_id}: {e}")
            raise
        finally:
            session.close()

    def query_events(
        self,
        page: int = 1,
        page_size: int = 200,
        level: Optional[str] = None,
        source_system: Optional[str] = None,
        component: Optional[str] = None,
        search_term: Optional[str] = None,
        batch_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Executes server-side pagination and SQL filtering.
        Returns (list_of_event_dicts, total_matching_count).
        """
        session = self.Session()
        try:
            query = session.query(EventModel)

            # Apply filters
            if level and level != "ALL":
                query = query.filter(EventModel.level == level)
            if source_system and source_system != "ALL":
                query = query.filter(EventModel.source_system == source_system)
            if component and component != "ALL":
                query = query.filter(EventModel.component == component)
            if batch_id:
                query = query.filter(EventModel.batch_id == batch_id)
            if search_term:
                term = f"%{search_term}%"
                query = query.filter(
                    (EventModel.message.like(term)) |
                    (EventModel.raw.like(term)) |
                    (EventModel.component.like(term))
                )
            if date_from:
                query = query.filter(EventModel.ts_utc >= date_from)
            if date_to:
                query = query.filter(EventModel.ts_utc <= date_to)

            total_count = query.count()

            # Server-side pagination
            offset = (page - 1) * page_size
            events = query.order_by(EventModel.ts_utc.desc(), EventModel.line_no.asc()).offset(offset).limit(page_size).all()

            results = []
            for e in events:
                results.append({
                    "event_id": e.event_id,
                    "batch_id": e.batch_id,
                    "file_name": e.file_name,
                    "line_no": e.line_no,
                    "ts_utc": e.ts_utc,
                    "ts_raw": e.ts_raw,
                    "ts_confidence": e.ts_confidence,
                    "level": e.level,
                    "source_system": e.source_system,
                    "component": e.component,
                    "message": e.message,
                    "raw": e.raw,
                    "attributes": json.loads(e.attributes) if e.attributes else {}
                })

            return results, total_count
        finally:
            session.close()

    def get_event_by_id(self, event_id: str) -> Optional[Dict[str, Any]]:
        session = self.Session()
        try:
            e = session.query(EventModel).filter(EventModel.event_id == event_id).first()
            if not e:
                return None
            return {
                "event_id": e.event_id,
                "batch_id": e.batch_id,
                "file_name": e.file_name,
                "line_no": e.line_no,
                "ts_utc": e.ts_utc,
                "ts_raw": e.ts_raw,
                "ts_confidence": e.ts_confidence,
                "level": e.level,
                "source_system": e.source_system,
                "component": e.component,
                "message": e.message,
                "raw": e.raw,
                "attributes": json.loads(e.attributes) if e.attributes else {}
            }
        finally:
            session.close()

    def get_surrounding_context(self, event_id: str, context_lines: int = 5) -> List[Dict[str, Any]]:
        """Returns surrounding log events from the same file around line_no."""
        target = self.get_event_by_id(event_id)
        if not target:
            return []

        session = self.Session()
        try:
            line = target["line_no"]
            file_name = target["file_name"]
            events = session.query(EventModel).filter(
                EventModel.file_name == file_name,
                EventModel.line_no >= max(1, line - context_lines),
                EventModel.line_no <= line + context_lines
            ).order_by(EventModel.line_no.asc()).all()

            results = []
            for e in events:
                results.append({
                    "event_id": e.event_id,
                    "line_no": e.line_no,
                    "level": e.level,
                    "message": e.message,
                    "raw": e.raw,
                    "is_target": e.event_id == event_id
                })
            return results
        finally:
            session.close()

    def get_severity_counts(self) -> Dict[str, int]:
        session = self.Session()
        try:
            res = session.query(EventModel.level, text("count(*)")).group_by(EventModel.level).all()
            return {r[0]: r[1] for r in res}
        finally:
            session.close()

    def get_source_distribution(self) -> Dict[str, int]:
        session = self.Session()
        try:
            res = session.query(EventModel.source_system, text("count(*)")).group_by(EventModel.source_system).all()
            return {r[0]: r[1] for r in res}
        finally:
            session.close()

    def get_hourly_distribution(self) -> List[Dict[str, Any]]:
        session = self.Session()
        try:
            # SQLite strftime for hourly aggregation
            sql = text("SELECT strftime('%Y-%m-%d %H:00', ts_utc) as hour, level, count(*) as count FROM events WHERE ts_utc IS NOT NULL GROUP BY hour, level ORDER BY hour ASC")
            res = session.execute(sql).fetchall()
            return [{"hour": r[0], "level": r[1], "count": r[2]} for r in res]
        finally:
            session.close()

    def get_distinct_values(self, column_name: str) -> List[str]:
        session = self.Session()
        try:
            if column_name == "level":
                res = session.query(EventModel.level).distinct().all()
            elif column_name == "source_system":
                res = session.query(EventModel.source_system).distinct().all()
            elif column_name == "component":
                res = session.query(EventModel.component).distinct().all()
            else:
                return []
            return [r[0] for r in res if r[0] is not None]
        finally:
            session.close()

    def list_batches(self) -> List[Dict[str, Any]]:
        session = self.Session()
        try:
            batches = session.query(BatchModel).order_by(BatchModel.uploaded_at.desc()).all()
            return [
                {
                    "batch_id": b.batch_id,
                    "file_name": b.file_name,
                    "file_size_bytes": b.file_size_bytes,
                    "total_events": b.total_events,
                    "matched_profile": b.matched_profile,
                    "matched_profile_version": b.matched_profile_version,
                    "match_ratio": b.match_ratio,
                    "uploaded_at": b.uploaded_at
                }
                for b in batches
            ]
        finally:
            session.close()

    def get_profile_versions_for_batches(self, batch_ids: List[str]) -> Dict[str, str]:
        """Return {profile_name: profile_version} for the given batch IDs.
        Used by the analysis pipeline to stamp parser provenance on
        every response."""
        if not batch_ids:
            return {}
        session = self.Session()
        try:
            rows = (
                session.query(BatchModel.matched_profile, BatchModel.matched_profile_version)
                .filter(BatchModel.batch_id.in_(batch_ids))
                .distinct()
                .all()
            )
            return {
                row.matched_profile: row.matched_profile_version
                for row in rows
                if row.matched_profile
            }
        finally:
            session.close()

    def clear_all(self) -> None:
        """Deletes every batch (and, via ON DELETE CASCADE, every event).
        Destructive -- callers are responsible for authorization (this is
        gated to admins at the API layer, not here)."""
        session = self.Session()
        try:
            session.query(EventModel).delete()
            session.query(BatchModel).delete()
            session.commit()
            logger.warning("All log data cleared from the database.")
        finally:
            session.close()

    def delete_batch(self, batch_id: str) -> bool:
        """Delete a single batch and its events (via ON DELETE CASCADE).
        Returns True if the batch existed and was deleted, False if not found."""
        session = self.Session()
        try:
            batch = session.query(BatchModel).filter_by(batch_id=batch_id).first()
            if not batch:
                return False
            session.delete(batch)
            session.commit()
            logger.info("Deleted batch %s (%s)", batch_id, batch.file_name)
            return True
        finally:
            session.close()

    def get_component_error_counts(self) -> Dict[str, int]:
        """ERROR/CRITICAL event counts grouped by component. Used by the
        (heuristic, not ML) anomaly report -- a real SQL aggregate, not a
        pull-everything-into-Python scan."""
        session = self.Session()
        try:
            res = (
                session.query(EventModel.component, text("count(*)"))
                .filter(EventModel.level.in_(["ERROR", "CRITICAL"]))
                .group_by(EventModel.component)
                .all()
            )
            return {(r[0] or "unknown"): r[1] for r in res}
        finally:
            session.close()

    def get_events_for_analysis(
        self,
        source_system: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50000,
    ) -> List[Dict[str, Any]]:
        """Bounded, ascending-time-order fetch for cross-event analysis
        (timing/gap/correlation computations that need to see a whole
        ordered window at once, unlike query_events' UI-pagination
        contract, which is newest-first and page-oriented).

        Scoped by source_system and an optional date range so this stays a
        bounded query -- callers doing long-range or all-time analysis on a
        genuinely large dataset should page through date ranges rather than
        rely on `limit` alone; `limit` is a safety cap, not a substitute for
        a sensible time window."""
        session = self.Session()
        try:
            query = session.query(EventModel).filter(EventModel.source_system == source_system)
            if date_from:
                query = query.filter(EventModel.ts_utc >= date_from)
            if date_to:
                query = query.filter(EventModel.ts_utc <= date_to)

            events = query.order_by(EventModel.ts_utc.asc(), EventModel.line_no.asc()).limit(limit).all()

            return [
                {
                    "event_id": e.event_id,
                    "batch_id": e.batch_id,
                    "file_name": e.file_name,
                    "line_no": e.line_no,
                    "ts_utc": e.ts_utc,
                    "ts_raw": e.ts_raw,
                    "ts_confidence": e.ts_confidence,
                    "level": e.level,
                    "source_system": e.source_system,
                    "component": e.component,
                    "message": e.message,
                    "raw": e.raw,
                    "attributes": json.loads(e.attributes) if e.attributes else {},
                }
                for e in events
            ]
        finally:
            session.close()
