import re
from collections import defaultdict

def parse_file(log_file_path):
    records_by_tracker = {}
    events = []
    event_row = {}

    with open(log_file_path, 'r') as file:
        content = file.read()

    # Each error entry spans multiple physical lines (the tracker line, the
    # exception line, and one or more "at ..." stack-trace lines), separated
    # from the next entry by a blank line -- read whole blocks instead of
    # single lines so the multi-line regexes below actually have a "\n" to
    # match against.
    for raw_block in re.split(r"\n\s*\n", content):
            block = raw_block.strip()
            if not block:
                continue
            line = block + "\n"

            # Determine event type based on log content
            if "Log Tracker No" in line:
                event_type = "error"
            else:
                event_type = "unknown"

            # Extract basic metadata
            timestamp = re.search(r"(\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M)", line)
            timestamp_str = timestamp.group(1) if timestamp else None

            # Parse error events
            if event_type == "error":
                tracker = re.search(r"Log Tracker No:\s*(\w+)", line)
                tracker_no = tracker.group(1) if tracker else None

                error_message = re.search(r"Error in (.+?) Call : (.+?)\n", line)
                error_msg = error_message.group(1) if error_message else None
                url = error_message.group(2) if error_message else None

                # Extract stack trace
                stack_trace = re.search(r"System\.(.+?)\:\s*(.+?)\n\s+at (.+?)\n", line, re.DOTALL)
                exception_type = stack_trace.group(1) if stack_trace else None
                exception_details = stack_trace.group(2) if stack_trace else None
                stack_trace_line = stack_trace.group(3) if stack_trace else None

                # Update records_by_tracker
                if tracker_no and tracker_no in records_by_tracker:
                    records_by_tracker[tracker_no]["error_message"] = error_msg
                    records_by_tracker[tracker_no]["url"] = url
                    records_by_tracker[tracker_no]["exception_type"] = exception_type
                    records_by_tracker[tracker_no]["exception_details"] = exception_details
                    records_by_tracker[tracker_no]["stack_trace"] = stack_trace_line
                else:
                    records_by_tracker[tracker_no] = {
                        "timestamp": timestamp_str,
                        "tracker_no": tracker_no,
                        "error_message": error_msg,
                        "url": url,
                        "exception_type": exception_type,
                        "exception_details": exception_details,
                        "stack_trace": stack_trace_line
                    }

                # Create event record
                event_row = {
                    "timestamp": timestamp_str,
                    "event_type": event_type,
                    "tracker_no": tracker_no,
                    "parsed": {
                        "error_message": error_msg,
                        "url": url,
                        "exception_type": exception_type,
                        "exception_details": exception_details,
                        "stack_trace": stack_trace_line
                    }
                }
                events.append(event_row)

            # Handle other event types (if needed)
            # Add additional parsing logic here for different event types

    return records_by_tracker, events


def build_summary(records, events):
    summary = {
        "total_events": len(events),
        "by_event_type": defaultdict(int),
        "by_exception_type": defaultdict(int),
        "by_tracker": defaultdict(int)
    }

    # Count event types
    for event in events:
        summary["by_event_type"][event["event_type"]] += 1

    # Count exceptions by type
    for event in events:
        if event["event_type"] == "error":
            error_data = event["parsed"]
            if error_data["exception_type"]:
                summary["by_exception_type"][error_data["exception_type"]] += 1

    # Count records by tracker
    for tracker in records.values():
        if tracker["tracker_no"]:
            summary["by_tracker"][tracker["tracker_no"]] += 1

    return dict(summary)


# ---------------------------------------------------------------------------
# LLens integration -- the argparse-free CLI block / __main__ guard was
# dropped (consistent with the other custom parsers in this directory), and
# parse_file()'s file-reading loop was fixed to read multi-line error blocks
# (tracker line + exception line + stack-trace line, separated by a blank
# line) instead of one physical line at a time. As originally written, every
# regex that needed content spanning more than one line (error_message, url,
# exception_type, exception_details, stack_trace) could never match, because
# it was matched against a single already-.strip()'d line with no embedded
# "\n" for those patterns to find. Nothing else was changed -- same
# regexes, same field names, same records_by_tracker/events shape,
# build_summary() untouched.
#
# Still-known, unfixed behavior: only blocks containing "Log Tracker No"
# ever become events -- everything else is silently skipped (parse_file()
# doesn't track or count them at all).
# ---------------------------------------------------------------------------

DISPLAY_NAME = "Debit Portal Error Log (Stack Trace)"
DEFAULT_SOURCE_SYSTEM = "debit_portal_error_log"

_TIMESTAMP_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M")


def detect(sample_text: str) -> bool:
    lowered = sample_text.lower()
    has_error_marker = "log tracker no" in lowered and ("error in " in lowered or "system." in lowered)
    return has_error_marker and bool(_TIMESTAMP_RE.search(sample_text))


def parse_log_file(log_file_path, output_json_path=None):
    """Adapter for the LLens custom-parser registry. Wraps the original,
    unmodified parse_file() above and flattens its (records_by_tracker,
    events) output into the flat record shape the registry expects (see
    custom_parser_registry.py). No original parsing logic was touched to
    build this adapter -- it exists only to let the parser above be run
    through LLens for manual verification.

    _raw_block is reconstructed from the parsed fields since parse_file()
    doesn't retain the original line text -- not necessarily byte-identical
    to the source log line.
    """
    records, events = parse_file(log_file_path)

    out_records = []
    for event in events:
        tracker = event.get("tracker_no")
        parsed = event.get("parsed") or {}
        record = records.get(tracker) if tracker else None

        action = parsed.get("error_message") or "Error event"
        details = {"parsed": parsed}
        if record:
            details["record"] = record

        out_records.append(
            {
                "timestamp": event.get("timestamp"),
                "log_level": "ERROR",
                "correlation_id": tracker,
                "log_type": event.get("event_type"),
                "action": action,
                "details": details,
                "_raw_block": f'{event.get("timestamp")} Log Tracker No: {tracker} => {action}',
            }
        )

    if output_json_path:
        import json
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(out_records, f, indent=2, default=str)

    return out_records
