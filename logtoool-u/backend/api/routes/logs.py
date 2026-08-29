import csv
import io
import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from backend.api.config import settings
from backend.api.deps import (
    get_alert_processor,
    get_current_user,
    get_db,
    get_ingestion_engine,
    get_profile_manager,
    require_admin,
)
from backend.auth.service import AuthenticatedUser
from backend.core.ingest import LogIngestionEngine
from backend.core.custom_parser_registry import get_custom_profile_by_name
from backend.core.profiles import ProfileManager
from backend.core.profiling import compute_anomaly_report
from backend.core.store import DatabaseManager
from backend.alerts.rules import AlertRulesProcessor

logger = logging.getLogger("logtool.api.logs")

router = APIRouter(prefix="/api/logs", tags=["logs"])

# Only files with these extensions get read during directory ingestion --
# this is a server-local filesystem walk (unlike browser upload), so it's
# scoped to recognizable log file types rather than reading anything a path
# happens to contain. Deliberately narrower than the single-file upload
# picker: bare ".json" is excluded here because a directory scan is far
# more likely to sweep up an unrelated single-object JSON config file
# (settings.json, package.json, etc.) that isn't line-delimited log data
# and would just produce parse garbage -- ".jsonl" unambiguously means
# JSON-lines log data, so it stays.
ALLOWED_DIRECTORY_EXTENSIONS = {".log", ".txt", ".jsonl", ".csv", ".tsv"}


class IngestDirectoryRequest(BaseModel):
    path: str
    recursive: bool = True


@router.post("/upload")
async def upload_log(
    file: UploadFile,
    profile_name: Optional[str] = None,
    _user: AuthenticatedUser = Depends(get_current_user),
    ingestion: LogIngestionEngine = Depends(get_ingestion_engine),
    profile_manager: ProfileManager = Depends(get_profile_manager),
    db: DatabaseManager = Depends(get_db),
    alerts: AlertRulesProcessor = Depends(get_alert_processor),
):
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    # UploadFile wraps a SpooledTemporaryFile -- checking size without a
    # separate read pass: seek to end, read position, seek back.
    file.file.seek(0, 2)
    size_bytes = file.file.tell()
    file.file.seek(0)
    if size_bytes > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File is {size_bytes / 1024 / 1024:.1f} MB, exceeds the "
            f"{settings.max_upload_size_mb} MB limit. Split it and upload in smaller pieces.",
        )

    forced_profile = None
    if profile_name:
        forced_profile = profile_manager.get_profile_by_name(profile_name)
        if not forced_profile:
            forced_profile = get_custom_profile_by_name(profile_name)
        if not forced_profile:
            raise HTTPException(status_code=404, detail=f"Unknown profile '{profile_name}'")

    summary = ingestion.ingest_file_stream(
        file_obj=file.file,
        file_name=file.filename or "upload.log",
        file_size_bytes=size_bytes,
        forced_profile=forced_profile,
    )

    # Evaluate alert rules against whatever was just ingested -- alerts are
    # triggered by ingestion, not by any UI page being open.
    if summary.parsed_events_count > 0:
        events, _ = db.query_events(page=1, page_size=summary.parsed_events_count, batch_id=summary.batch_id)
        try:
            alerts.evaluate_batch_alerts(batch_id=summary.batch_id, events=events)
        except Exception:
            logger.exception("Alert evaluation failed for batch %s (ingestion itself succeeded)", summary.batch_id)
        try:
            alerts.evaluate_anomaly_rules(db)
        except Exception:
            logger.exception("Anomaly alert evaluation failed for batch %s (ingestion itself succeeded)", summary.batch_id)

    return summary.model_dump()


@router.post("/ingest-directory")
def ingest_directory(
    body: IngestDirectoryRequest,
    _admin: AuthenticatedUser = Depends(require_admin),
    ingestion: LogIngestionEngine = Depends(get_ingestion_engine),
    db: DatabaseManager = Depends(get_db),
    alerts: AlertRulesProcessor = Depends(get_alert_processor),
):
    """Ingests every recognized log file under a server-local directory
    path -- the user just points this at a directory, nothing gets
    uploaded through the browser. Admin-only: unlike a browser upload
    (which only ever sees content the user already has), this lets the
    app read arbitrary paths the server process can see, which is a
    meaningfully different trust boundary.
    """
    dir_path = Path(body.path)

    if not dir_path.exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {body.path}")
    if not dir_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {body.path}")

    if body.recursive:
        candidate_files = [p for p in dir_path.rglob("*") if p.is_file()]
    else:
        candidate_files = [p for p in dir_path.iterdir() if p.is_file()]

    log_files = sorted(p for p in candidate_files if p.suffix.lower() in ALLOWED_DIRECTORY_EXTENSIONS)
    skipped = sorted(str(p) for p in candidate_files if p.suffix.lower() not in ALLOWED_DIRECTORY_EXTENSIONS)

    if not log_files:
        raise HTTPException(
            status_code=404,
            detail=f"No files with a recognized log extension ({', '.join(sorted(ALLOWED_DIRECTORY_EXTENSIONS))}) "
            f"found under {body.path}",
        )

    ingested = []
    errors = []

    for f in log_files:
        try:
            size = f.stat().st_size
            if size > settings.max_upload_size_mb * 1024 * 1024:
                errors.append(f"{f}: skipped, {size / 1024 / 1024:.1f} MB exceeds the {settings.max_upload_size_mb} MB limit")
                continue
            with open(f, "rb") as fh:
                summary = ingestion.ingest_file_stream(file_obj=fh, file_name=str(f), file_size_bytes=size)
            ingested.append(summary.model_dump())

            if summary.parsed_events_count > 0:
                events, _ = db.query_events(page=1, page_size=summary.parsed_events_count, batch_id=summary.batch_id)
                try:
                    alerts.evaluate_batch_alerts(batch_id=summary.batch_id, events=events)
                except Exception:
                    logger.exception("Alert evaluation failed for batch %s", summary.batch_id)
                try:
                    alerts.evaluate_anomaly_rules(db)
                except Exception:
                    logger.exception("Anomaly alert evaluation failed for batch %s", summary.batch_id)
        except Exception as e:
            logger.exception("Failed to ingest %s during directory scan", f)
            errors.append(f"{f}: {e}")

    logger.info(
        "Directory ingestion of %s complete: %d file(s) ingested, %d error(s), %d skipped (unrecognized extension)",
        body.path, len(ingested), len(errors), len(skipped),
    )

    return {"ingested": ingested, "errors": errors, "skipped_unrecognized_extension": skipped}


@router.post("/ingest-sample")
def ingest_sample(
    _user: AuthenticatedUser = Depends(get_current_user),
    ingestion: LogIngestionEngine = Depends(get_ingestion_engine),
    db: DatabaseManager = Depends(get_db),
    alerts: AlertRulesProcessor = Depends(get_alert_processor),
):
    samples_dir = Path(__file__).resolve().parents[2] / "samples"
    sample_files = sorted(samples_dir.glob("*.jsonl")) + sorted(samples_dir.glob("*.log"))
    if not sample_files:
        raise HTTPException(status_code=404, detail="No sample files bundled with this install")

    summaries = []
    for f in sample_files:
        with open(f, "rb") as fh:
            size = f.stat().st_size
            summary = ingestion.ingest_file_stream(file_obj=fh, file_name=f.name, file_size_bytes=size)
            summaries.append(summary.model_dump())

            if summary.parsed_events_count > 0:
                events, _ = db.query_events(page=1, page_size=summary.parsed_events_count, batch_id=summary.batch_id)
                try:
                    alerts.evaluate_batch_alerts(batch_id=summary.batch_id, events=events)
                except Exception:
                    logger.exception("Alert evaluation failed for sample batch %s", summary.batch_id)
                try:
                    alerts.evaluate_anomaly_rules(db)
                except Exception:
                    logger.exception("Anomaly alert evaluation failed for sample batch %s", summary.batch_id)
    return {"ingested": summaries}


@router.get("/events")
def get_events(
    page: int = Query(1, ge=1),
    pageSize: int = Query(200, ge=1, le=5000),
    level: Optional[str] = None,
    source_system: Optional[str] = None,
    component: Optional[str] = None,
    search_term: Optional[str] = None,
    batch_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    events, total = db.query_events(
        page=page,
        page_size=pageSize,
        level=level,
        source_system=source_system,
        component=component,
        search_term=search_term,
        batch_id=batch_id,
        date_from=date_from,
        date_to=date_to,
    )
    return {"events": events, "total": total, "page": page, "pageSize": pageSize}


# Same cap as GET /events' own pageSize ceiling -- exporting the full
# filtered result set (not just the visible page) is the point of this
# endpoint, but it still needs a bound. The frontend already has `total`
# from its own /events call and shows a "narrow your filters" notice
# itself when it exceeds this, rather than the export silently truncating
# with no indication anything was left out.
EXPORT_MAX_ROWS = 5000

_EXPORT_CSV_FIELDS = [
    "event_id", "batch_id", "file_name", "line_no", "ts_utc", "ts_raw",
    "ts_confidence", "level", "source_system", "component", "message", "raw", "attributes",
]


@router.get("/events/export")
def export_events(
    format: str = Query("csv", pattern="^(csv|json)$"),
    level: Optional[str] = None,
    source_system: Optional[str] = None,
    component: Optional[str] = None,
    search_term: Optional[str] = None,
    batch_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    events, total = db.query_events(
        page=1,
        page_size=EXPORT_MAX_ROWS,
        level=level,
        source_system=source_system,
        component=component,
        search_term=search_term,
        batch_id=batch_id,
        date_from=date_from,
        date_to=date_to,
    )

    if format == "json":
        payload = {"events": events, "total": total, "exported": len(events)}
        return Response(
            content=json.dumps(payload, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=llens-events.json"},
        )

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_EXPORT_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for e in events:
        row = dict(e)
        row["attributes"] = json.dumps(row.get("attributes") or {}, default=str)
        writer.writerow(row)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=llens-events.csv"},
    )


@router.get("/context/{event_id}")
def get_context(
    event_id: str,
    context_lines: int = Query(5, ge=1, le=50),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    event = db.get_event_by_id(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    context = db.get_surrounding_context(event_id, context_lines=context_lines)
    return {"event": event, "context": context}


@router.get("/stats")
def get_stats(
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    return {
        "severity_counts": db.get_severity_counts(),
        "source_distribution": db.get_source_distribution(),
        "hourly_distribution": db.get_hourly_distribution(),
        "batches": db.list_batches(),
    }


@router.get("/batches")
def get_batches(
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    return db.list_batches()


@router.delete("/clear")
def clear_logs(
    _admin: AuthenticatedUser = Depends(require_admin),
    db: DatabaseManager = Depends(get_db),
):
    db.clear_all()
    return {"ok": True}


@router.get("/profiling")
def get_profiling(
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    """Statistical anomaly flagging -- see core/profiling.py docstring:
    this is honest z-score heuristics, not a trained ML model."""
    return compute_anomaly_report(db)


@router.post("/ml/train")
def recompute_profiling(
    _user: AuthenticatedUser = Depends(get_current_user),
    db: DatabaseManager = Depends(get_db),
):
    """Kept at this URL for frontend compatibility with the pre-existing
    'train' button, but there is no model and nothing is actually trained --
    this just recomputes the same on-demand statistical snapshot as GET
    /profiling. The frontend copy for this button is updated in step 5 to
    stop implying otherwise."""
    return compute_anomaly_report(db)
