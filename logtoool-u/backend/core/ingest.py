"""
Log Streaming and Ingestion Processor.
Streams large log files without loading multi-GB files into memory.
Performs automatic profile detection, multiline grouping, and database insertion.
"""

import os
import tempfile
from typing import BinaryIO, Callable, List, Optional, Tuple
import logging
from datetime import datetime, timezone

from backend.core.schema import (
    BatchRecord,
    CanonicalLogEvent,
    IngestionSummary,
    ParserProfile,
    ProfileType,
)
from backend.core.parse import (
    GroupedLineEvent,
    evaluate_profile_match,
    group_multiline_logs,
    parse_event_with_profile,
    select_best_profile,
)
from backend.core.profiles import ProfileManager
from backend.core.store import DatabaseManager
from backend.core import custom_parser_registry

logger = logging.getLogger("logtool.ingest")


class LogIngestionEngine:
    """
    Handles streaming file ingestion, parser profile detection, and batch storage.
    """
    def __init__(self, db_manager: DatabaseManager, profile_manager: ProfileManager, batch_size: int = 1000):
        self.db_manager = db_manager
        self.profile_manager = profile_manager
        self.batch_size = batch_size

    def ingest_file_stream(
        self,
        file_obj: BinaryIO,
        file_name: str,
        file_size_bytes: int,
        forced_profile: Optional[ParserProfile] = None,
        progress_callback: Optional[Callable[[float], None]] = None
    ) -> IngestionSummary:
        """
        Streams file line by line, detects or uses parser profile, parses events, and stores in SQLite.
        """
        upload_time = datetime.now(timezone.utc)
        batch_record = BatchRecord(
            file_name=file_name,
            file_size_bytes=file_size_bytes,
            uploaded_at=upload_time.isoformat()
        )

        warnings: List[str] = []

        # Step 1: Read sample lines for profile detection (first ~500 lines)
        sample_lines: List[Tuple[int, str]] = []
        file_obj.seek(0)
        
        line_counter = 0
        for line_bytes in file_obj:
            line_counter += 1
            line_str = line_bytes.decode("utf-8", errors="replace")
            sample_lines.append((line_counter, line_str))
            if line_counter >= 500:
                break

        if not sample_lines:
            return IngestionSummary(
                batch_id=batch_record.batch_id,
                file_name=file_name,
                total_lines=0,
                grouped_events_count=0,
                parsed_events_count=0,
                matched_profile="None",
                match_ratio=0.0,
                warnings=["Uploaded file was empty."]
            )

        # Step 2: Determine Parser Profile
        active_profile: Optional[ParserProfile] = forced_profile
        match_ratio = 0.0

        if not active_profile:
            profiles = self.profile_manager.list_profiles()
            grouped_sample = group_multiline_logs(sample_lines)
            active_profile, match_ratio = select_best_profile(grouped_sample, profiles)
            if active_profile:
                logger.info(
                    "Profile detection: matched '%s' with score %.3f (threshold %.3f)",
                    active_profile.name, match_ratio, active_profile.min_match_ratio,
                )
            else:
                logger.info("Profile detection: no declarative profile matched")

        if not active_profile:
            # Before falling back to a possibly-wrong declarative profile,
            # try the code-defined custom parsers (formats too complex for
            # the regex/json/delimited system -- see custom_parser_registry.py).
            sample_text = "".join(line for _, line in sample_lines)
            custom_profile, custom_warnings = custom_parser_registry.detect_custom_parser(sample_text)
            if custom_profile:
                active_profile = custom_profile
                match_ratio = 1.0  # recomputed below once the full file is parsed
                warnings.extend(custom_warnings)
                logger.info("Profile detection: matched custom parser '%s'", custom_profile.name)

        if not active_profile:
            # Fallback profile if none matched
            active_profile = self.profile_manager.list_profiles()[0]
            grouped_sample = group_multiline_logs(sample_lines)
            match_ratio, _ = evaluate_profile_match(grouped_sample, active_profile)
            warnings.append(
                f"No parser profile met threshold {active_profile.min_match_ratio}. "
                f"Using default fallback '{active_profile.name}' with match ratio {match_ratio:.2f}."
            )
            logger.warning(
                "Profile detection: fallback to '%s' with score %.3f",
                active_profile.name, match_ratio,
            )

        batch_record.matched_profile = active_profile.name
        batch_record.matched_profile_version = active_profile.profile_version
        batch_record.match_ratio = round(match_ratio, 3)

        # Custom-parser profiles need the whole file content up front (their
        # parsing algorithms split on patterns across the full document) and
        # don't participate in the line-by-line streaming loop below at all.
        if active_profile.type == ProfileType.CUSTOM:
            file_obj.seek(0)
            raw_text = file_obj.read().decode("utf-8", errors="replace")
            total_lines = raw_text.count("\n") + (1 if raw_text and not raw_text.endswith("\n") else 0)

            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8") as tf:
                    tf.write(raw_text)
                    tmp_path = tf.name
                parsed_events = custom_parser_registry.run_custom_parser(
                    profile=active_profile,
                    file_path=tmp_path,
                    batch_id=batch_record.batch_id,
                    file_name=file_name,
                    upload_time=upload_time,
                )
            finally:
                if tmp_path:
                    os.unlink(tmp_path)

            total_parsed_count = len(parsed_events)
            match_ratio = 1.0 if parsed_events else 0.0
            if not parsed_events:
                warnings.append(f"Custom parser '{active_profile.name}' produced zero records from this file.")

            batch_record.match_ratio = round(match_ratio, 3)
            batch_record.total_events = total_parsed_count
            self.db_manager.insert_batch_and_events(batch_record, parsed_events)

            if progress_callback:
                progress_callback(1.0)

            logger.info(
                f"Completed custom-parser ingestion for {file_name}: "
                f"{total_parsed_count} events via '{active_profile.name}'."
            )

            return IngestionSummary(
                batch_id=batch_record.batch_id,
                file_name=file_name,
                total_lines=total_lines,
                grouped_events_count=total_parsed_count,
                parsed_events_count=total_parsed_count,
                matched_profile=active_profile.name,
                match_ratio=round(match_ratio, 3),
                warnings=warnings,
            )

        # Step 3: Full Streaming Pass
        file_obj.seek(0)
        
        line_no = 0
        current_chunk: List[Tuple[int, str]] = []
        parsed_events: List[CanonicalLogEvent] = []
        total_grouped_count = 0
        total_parsed_count = 0

        for line_bytes in file_obj:
            line_no += 1
            line_str = line_bytes.decode("utf-8", errors="replace")
            current_chunk.append((line_no, line_str))

            # Process in streaming chunks of lines
            if len(current_chunk) >= self.batch_size:
                grouped_chunk = group_multiline_logs(current_chunk, profile=active_profile)
                total_grouped_count += len(grouped_chunk)

                for item in grouped_chunk:
                    evt, valid_ts = parse_event_with_profile(
                        grouped_event=item,
                        profile=active_profile,
                        batch_id=batch_record.batch_id,
                        file_name=file_name,
                        upload_time=upload_time
                    )
                    if evt:
                        parsed_events.append(evt)
                        total_parsed_count += 1

                current_chunk = []
                if progress_callback and file_size_bytes > 0:
                    current_pos = file_obj.tell()
                    progress_callback(min(0.95, current_pos / float(file_size_bytes)))

        # Process remaining chunk
        if current_chunk:
            grouped_chunk = group_multiline_logs(current_chunk, profile=active_profile)
            total_grouped_count += len(grouped_chunk)

            for item in grouped_chunk:
                evt, valid_ts = parse_event_with_profile(
                    grouped_event=item,
                    profile=active_profile,
                    batch_id=batch_record.batch_id,
                    file_name=file_name,
                    upload_time=upload_time
                )
                if evt:
                    parsed_events.append(evt)
                    total_parsed_count += 1

        # Step 4: Database Storage
        batch_record.total_events = total_parsed_count
        self.db_manager.insert_batch_and_events(batch_record, parsed_events)

        if progress_callback:
            progress_callback(1.0)

        logger.info(
            f"Completed ingestion for {file_name}: {total_parsed_count} events stored using profile '{active_profile.name}'."
        )

        return IngestionSummary(
            batch_id=batch_record.batch_id,
            file_name=file_name,
            total_lines=line_no,
            grouped_events_count=total_grouped_count,
            parsed_events_count=total_parsed_count,
            matched_profile=active_profile.name,
            match_ratio=round(match_ratio, 3),
            warnings=warnings
        )
