# Adapted from the customer-provided OTP Online Processor log parser.
# Every function below down to save_json() is UNCHANGED from the original
# source -- only the argparse-based CLI (main()/__main__ block) was removed
# (consistent with the other custom parsers in this directory) and a new
# parse_log_file() adapter was added at the bottom to flatten this parser's
# richer output (per-tracker merged records + a flat per-event stream) into
# the flat record shape the LLens custom-parser registry expects (see
# custom_parser_registry.py). No original parsing logic was touched to
# build that adapter.
import re
import json
import html
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from collections import defaultdict

TIMESTAMP_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M")


def clean_text(value):
    if value is None:
        return ""

    return str(value).replace("\x00", "").replace("﻿", "").strip()


def parse_timestamp(value):
    try:
        return datetime.strptime(value, "%m/%d/%Y %I:%M:%S %p").isoformat(sep=" ")
    except Exception:
        return value


def to_float(value):
    value = clean_text(value)

    if value == "":
        return None

    try:
        return float(value)
    except Exception:
        return value


def split_timestamped_events(text):
    """
    Splits logs even if the file is one huge line.

    Supports:
      8/17/2026 3:00:10 PM : Msg Received-- <Msg>...</Msg>
      8/17/2026 3:00:10 PM : Message for Queue=mq-oab-otp-in-push
      8/17/2026 3:00:10 PM Log Tracker No: IA... => SMS Input Message: <Msg>...</Msg>
    """

    text = clean_text(text)
    matches = list(TIMESTAMP_RE.finditer(text))
    events = []

    for i, match in enumerate(matches):
        timestamp_raw = match.group()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)

        raw = clean_text(text[start:end])

        if raw.startswith(":"):
            raw = clean_text(raw[1:])

        events.append(
            {
                "event_no": i + 1,
                "timestamp_raw": timestamp_raw,
                "timestamp": parse_timestamp(timestamp_raw),
                "raw": raw,
            }
        )

    return events


def extract_tracker_from_text(raw):
    raw_decoded = html.unescape(raw)

    patterns = [
        r"Log Tracker No:\s*([A-Z]{2}\d+)",
        r"<mtrackingid>(.*?)</mtrackingid>",
    ]

    for pattern in patterns:
        match = re.search(pattern, raw_decoded, flags=re.IGNORECASE)
        if match:
            return clean_text(match.group(1))

    return None


def classify_event(raw):
    decoded = html.unescape(raw)
    decoded_lower = decoded.lower()
    raw_lower = raw.lower()

    if "msg received--" in decoded_lower and "<msg>" in decoded_lower:
        return "msg_received_sms_xml"

    if "sms input message" in decoded_lower and "<msg>" in decoded_lower:
        return "sms_input_xml"

    if (
        "sendemail mqemailsmg message" in decoded_lower
        and "<emailmsg>" in decoded_lower
    ):
        return "email_xml"

    if raw_lower.startswith("message for queue="):
        return "queue"

    if "message for queue=" in raw_lower:
        return "queue"

    if "otp processed successfully" in raw_lower:
        return "otp_success"

    if "sms placed in queue" in raw_lower:
        return "sms_queue_msg_id"

    if "force verify by mob" in raw_lower:
        return "force_verify_by_mobile"

    return "other"


def extract_xml(raw):
    """
    Extracts XML from escaped or normal text.

    Examples:
      Msg Received-- &lt;Msg&gt;...&lt;/Msg&gt;
      Msg Received-- <Msg>...</Msg>
      SMS Input Message: &lt;Msg&gt;...&lt;/Msg&gt;
      SendEmail ... &lt;EmailMsg&gt;...&lt;/EmailMsg&gt;
    """

    decoded = html.unescape(clean_text(raw))

    starts = []
    for tag in ["<Msg>", "<EmailMsg>"]:
        pos = decoded.find(tag)
        if pos != -1:
            starts.append(pos)

    if not starts:
        return ""

    start = min(starts)

    if decoded[start:].startswith("<Msg>"):
        end_tag = "</Msg>"
    else:
        end_tag = "</EmailMsg>"

    end = decoded.find(end_tag, start)

    if end == -1:
        return clean_text(decoded[start:])

    return clean_text(decoded[start : end + len(end_tag)])


def fix_bad_xml_ampersands(xml_text):
    """
    Fixes invalid XML caused by naked ampersands.

    Example:
      Pharmacy & Drug
    becomes:
      Pharmacy &amp; Drug
    """

    if not xml_text:
        return xml_text

    return re.sub(
        r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)", "&amp;", xml_text
    )


def extract_tag(xml_text, tag):
    """
    Regex fallback extraction for broken XML.
    """

    if not xml_text:
        return ""

    match = re.search(
        rf"<{tag}>(.*?)</{tag}>", xml_text, flags=re.IGNORECASE | re.DOTALL
    )

    if not match:
        return ""

    return clean_text(html.unescape(match.group(1)))


def safe_find(root, path):
    return clean_text(root.findtext(path))


def parse_msg_xml_et(xml_text):
    fixed_xml = fix_bad_xml_ampersands(xml_text)
    root = ET.fromstring(fixed_xml)

    if root.tag != "Msg":
        raise ValueError(f"Expected Msg XML but got {root.tag}")

    return {
        "tracker_no": safe_find(root, "./Header/mtrackingid"),
        "org": safe_find(root, "./Header/Org"),
        "type": safe_find(root, "./Header/Typ"),
        "lang": safe_find(root, "./Header/Lang"),
        "verify": safe_find(root, "./Header/Verify"),
        "mobile": safe_find(root, "./Header/Mobile"),
        "otp": safe_find(root, "./Body/OTP"),
        "masked_card": safe_find(root, "./Body/MaskedCardNo"),
        "otppan": safe_find(root, "./Body/OTPPAN"),
        "transaction": {
            "amount": to_float(safe_find(root, "./Body/TranAmount")),
            "currency": safe_find(root, "./Body/TranCurrency"),
            "date": safe_find(root, "./Body/TranDate"),
            "time": safe_find(root, "./Body/TranTime"),
        },
        "merchant": safe_find(root, "./Body/MerchantName"),
        "merchant_details": {
            "id": safe_find(root, "./Body/MerchantId") or None,
            "country_code": safe_find(root, "./Body/MerchantCountryCode") or None,
            "url": safe_find(root, "./Body/MerchantURL") or None,
        },
        "parse_method": "xml",
    }


def parse_msg_xml_regex(xml_text):
    """
    Fallback parser that still breaks the raw XML even if XML parser fails.
    """

    return {
        "tracker_no": extract_tag(xml_text, "mtrackingid"),
        "org": extract_tag(xml_text, "Org"),
        "type": extract_tag(xml_text, "Typ"),
        "lang": extract_tag(xml_text, "Lang"),
        "verify": extract_tag(xml_text, "Verify"),
        "mobile": extract_tag(xml_text, "Mobile"),
        "otp": extract_tag(xml_text, "OTP"),
        "masked_card": extract_tag(xml_text, "MaskedCardNo"),
        "otppan": extract_tag(xml_text, "OTPPAN"),
        "transaction": {
            "amount": to_float(extract_tag(xml_text, "TranAmount")),
            "currency": extract_tag(xml_text, "TranCurrency"),
            "date": extract_tag(xml_text, "TranDate"),
            "time": extract_tag(xml_text, "TranTime"),
        },
        "merchant": extract_tag(xml_text, "MerchantName"),
        "merchant_details": {
            "id": extract_tag(xml_text, "MerchantId") or None,
            "country_code": extract_tag(xml_text, "MerchantCountryCode") or None,
            "url": extract_tag(xml_text, "MerchantURL") or None,
        },
        "parse_method": "regex_fallback",
    }


def parse_msg_xml(xml_text):
    """
    Main SMS XML parser.

    It always tries to break the raw payload:
      1. XML parser after fixing naked &
      2. Regex fallback
      3. Fail only if mtrackingid cannot be extracted
    """

    xml_text = clean_text(xml_text)

    if not xml_text:
        return None, "No Msg XML found"

    try:
        parsed = parse_msg_xml_et(xml_text)

        if not parsed.get("tracker_no"):
            raise ValueError("Missing mtrackingid")

        return parsed, None

    except Exception as ex:
        parsed = parse_msg_xml_regex(xml_text)

        if parsed.get("tracker_no"):
            parsed["xml_parse_warning"] = str(ex)
            return parsed, None

        return None, str(ex)


def parse_email_xml_et(xml_text):
    fixed_xml = fix_bad_xml_ampersands(xml_text)
    root = ET.fromstring(fixed_xml)

    if root.tag != "EmailMsg":
        raise ValueError(f"Expected EmailMsg XML but got {root.tag}")

    return {
        "org": safe_find(root, "./Header/Org"),
        "email_to": safe_find(root, "./Header/EmailTo"),
        "type": safe_find(root, "./Header/Typ"),
        "lang": safe_find(root, "./Header/Lang"),
        "verify": safe_find(root, "./Header/Verify"),
        "otp": safe_find(root, "./Body/EMAILBODY1/MWTEXT/OTP"),
        "masked_card": safe_find(root, "./Body/EMAILBODY1/MWTEXT/MaskedCardNo"),
        "otppan": safe_find(root, "./Body/EMAILBODY1/MWTEXT/OTPPAN"),
        "transaction": {
            "amount": to_float(safe_find(root, "./Body/EMAILBODY1/MWTEXT/TranAmount")),
            "currency": safe_find(root, "./Body/EMAILBODY1/MWTEXT/TranCurrency"),
            "date": safe_find(root, "./Body/EMAILBODY1/MWTEXT/TranDate"),
            "time": safe_find(root, "./Body/EMAILBODY1/MWTEXT/TranTime"),
        },
        "merchant": safe_find(root, "./Body/EMAILBODY1/MWTEXT/MerchantName"),
        "merchant_details": {
            "id": safe_find(root, "./Body/EMAILBODY1/MWTEXT/MerchantId") or None,
            "country_code": safe_find(
                root, "./Body/EMAILBODY1/MWTEXT/MerchantCountryCode"
            )
            or None,
            "url": safe_find(root, "./Body/EMAILBODY1/MWTEXT/MerchantURL") or None,
        },
        "parse_method": "xml",
    }


def parse_email_xml_regex(xml_text):
    return {
        "org": extract_tag(xml_text, "Org"),
        "email_to": extract_tag(xml_text, "EmailTo"),
        "type": extract_tag(xml_text, "Typ"),
        "lang": extract_tag(xml_text, "Lang"),
        "verify": extract_tag(xml_text, "Verify"),
        "otp": extract_tag(xml_text, "OTP"),
        "masked_card": extract_tag(xml_text, "MaskedCardNo"),
        "otppan": extract_tag(xml_text, "OTPPAN"),
        "transaction": {
            "amount": to_float(extract_tag(xml_text, "TranAmount")),
            "currency": extract_tag(xml_text, "TranCurrency"),
            "date": extract_tag(xml_text, "TranDate"),
            "time": extract_tag(xml_text, "TranTime"),
        },
        "merchant": extract_tag(xml_text, "MerchantName"),
        "merchant_details": {
            "id": extract_tag(xml_text, "MerchantId") or None,
            "country_code": extract_tag(xml_text, "MerchantCountryCode") or None,
            "url": extract_tag(xml_text, "MerchantURL") or None,
        },
        "parse_method": "regex_fallback",
    }


def parse_email_xml(xml_text):
    xml_text = clean_text(xml_text)

    if not xml_text:
        return None, "No Email XML found"

    try:
        parsed = parse_email_xml_et(xml_text)
        return parsed, None

    except Exception as ex:
        parsed = parse_email_xml_regex(xml_text)

        if parsed.get("email_to") or parsed.get("otp") or parsed.get("merchant"):
            parsed["xml_parse_warning"] = str(ex)
            return parsed, None

        return None, str(ex)


def extract_queue(raw):
    match = re.search(r"Message for Queue\s*=\s*([^\s]+)", raw, flags=re.IGNORECASE)

    if match:
        return clean_text(match.group(1))

    return None


def extract_msg_id(raw):
    match = re.search(r"MsgId:\s*([A-Za-z0-9]+)", raw)

    if match:
        return clean_text(match.group(1))

    return None


def create_base_record(parsed, event):
    return {
        "tracker_no": parsed.get("tracker_no"),
        "org": parsed.get("org"),
        "mobile": parsed.get("mobile"),
        "otp": parsed.get("otp"),
        "masked_card": parsed.get("masked_card"),
        "otppan": parsed.get("otppan"),
        "transaction": parsed.get("transaction", {}),
        "merchant": parsed.get("merchant"),
        "merchant_details": parsed.get("merchant_details", {}),
        "timestamp": event["timestamp"],
        "timestamp_raw": event["timestamp_raw"],
        "queue": None,
        "email": None,
        "sms_msg_id": None,
        "otp_processed": False,
        "force_verify_by_mobile": False,
        "parse_method": parsed.get("parse_method"),
        "xml_parse_warning": parsed.get("xml_parse_warning"),
    }


def merge_record(existing, parsed):
    for key in [
        "org",
        "mobile",
        "otp",
        "masked_card",
        "otppan",
        "merchant",
        "parse_method",
        "xml_parse_warning",
    ]:
        value = parsed.get(key)

        if existing.get(key) in (None, "") and value not in (None, ""):
            existing[key] = value

    for key, value in parsed.get("transaction", {}).items():
        if existing["transaction"].get(key) in (None, "") and value not in (None, ""):
            existing["transaction"][key] = value

    for key, value in parsed.get("merchant_details", {}).items():
        if existing["merchant_details"].get(key) in (None, "") and value not in (
            None,
            "",
        ):
            existing["merchant_details"][key] = value


def merge_email(existing, parsed_email):
    if parsed_email.get("email_to") and not existing.get("email"):
        existing["email"] = parsed_email["email_to"]

    if not existing.get("merchant") and parsed_email.get("merchant"):
        existing["merchant"] = parsed_email["merchant"]

    if not existing.get("masked_card") and parsed_email.get("masked_card"):
        existing["masked_card"] = parsed_email["masked_card"]

    if not existing.get("otppan") and parsed_email.get("otppan"):
        existing["otppan"] = parsed_email["otppan"]

    for key, value in parsed_email.get("transaction", {}).items():
        if existing["transaction"].get(key) in (None, "") and value not in (None, ""):
            existing["transaction"][key] = value

    for key, value in parsed_email.get("merchant_details", {}).items():
        if existing["merchant_details"].get(key) in (None, "") and value not in (
            None,
            "",
        ):
            existing["merchant_details"][key] = value


def parse_file(input_file):
    input_file = Path(input_file)

    text = input_file.read_text(encoding="utf-8", errors="replace")

    timestamped_events = split_timestamped_events(text)

    records_by_tracker = {}
    events_output = []
    failed_events = []

    pending_tracker = None

    for event in timestamped_events:
        raw = clean_text(event["raw"])
        event_type = classify_event(raw)
        tracker_from_raw = extract_tracker_from_text(raw)

        event_row = {
            "event_no": event["event_no"],
            "timestamp": event["timestamp"],
            "timestamp_raw": event["timestamp_raw"],
            "event_type": event_type,
            "tracker_no": tracker_from_raw,
            "parsed": None,
            "raw": raw,
        }

        try:
            if event_type in ("msg_received_sms_xml", "sms_input_xml"):
                xml_text = extract_xml(raw)
                parsed, error = parse_msg_xml(xml_text)

                if error:
                    raise ValueError(error)

                tracker = parsed["tracker_no"]
                pending_tracker = tracker

                if tracker not in records_by_tracker:
                    records_by_tracker[tracker] = create_base_record(parsed, event)
                else:
                    merge_record(records_by_tracker[tracker], parsed)

                event_row["tracker_no"] = tracker
                event_row["parsed"] = parsed

            elif event_type == "email_xml":
                xml_text = extract_xml(raw)
                parsed_email, error = parse_email_xml(xml_text)

                if error:
                    raise ValueError(error)

                tracker = tracker_from_raw or pending_tracker

                if tracker and tracker in records_by_tracker:
                    merge_email(records_by_tracker[tracker], parsed_email)

                event_row["tracker_no"] = tracker
                event_row["parsed"] = parsed_email

            elif event_type == "queue":
                queue = extract_queue(raw)
                tracker = tracker_from_raw or pending_tracker

                if tracker and tracker in records_by_tracker:
                    records_by_tracker[tracker]["queue"] = queue

                event_row["tracker_no"] = tracker
                event_row["parsed"] = {"queue": queue}

            elif event_type == "sms_queue_msg_id":
                msg_id = extract_msg_id(raw)
                tracker = tracker_from_raw or pending_tracker

                if tracker and tracker in records_by_tracker:
                    records_by_tracker[tracker]["sms_msg_id"] = msg_id

                pending_tracker = tracker or pending_tracker

                event_row["tracker_no"] = tracker
                event_row["parsed"] = {"sms_msg_id": msg_id}

            elif event_type == "otp_success":
                tracker = tracker_from_raw or pending_tracker

                if tracker and tracker in records_by_tracker:
                    records_by_tracker[tracker]["otp_processed"] = True

                pending_tracker = tracker or pending_tracker

                event_row["tracker_no"] = tracker
                event_row["parsed"] = {"otp_processed": True}

            elif event_type == "force_verify_by_mobile":
                tracker = tracker_from_raw or pending_tracker

                if tracker and tracker in records_by_tracker:
                    records_by_tracker[tracker]["force_verify_by_mobile"] = True

                pending_tracker = tracker or pending_tracker

                event_row["tracker_no"] = tracker
                event_row["parsed"] = {"force_verify_by_mobile": True}

            else:
                failed_events.append(
                    {
                        "event_no": event["event_no"],
                        "timestamp": event["timestamp_raw"],
                        "reason": "Unknown event type",
                        "raw": raw,
                    }
                )

            events_output.append(event_row)

        except Exception as ex:
            failed_events.append(
                {
                    "event_no": event["event_no"],
                    "timestamp": event["timestamp_raw"],
                    "reason": str(ex),
                    "raw": raw,
                }
            )

            event_row["parsed"] = None
            events_output.append(event_row)

    return list(records_by_tracker.values()), events_output, failed_events


def build_summary(records, events, failed):
    by_org = defaultdict(int)
    by_queue = defaultdict(int)
    by_currency = defaultdict(int)
    by_merchant = defaultdict(int)
    event_types = defaultdict(int)
    parse_methods = defaultdict(int)

    for event in events:
        event_types[event["event_type"]] += 1

        parsed = event.get("parsed")
        if isinstance(parsed, dict):
            method = parsed.get("parse_method")
            if method:
                parse_methods[method] += 1

    for record in records:
        by_org[record.get("org") or "UNKNOWN"] += 1
        by_queue[record.get("queue") or "UNKNOWN"] += 1

        currency = record.get("transaction", {}).get("currency") or "UNKNOWN"
        by_currency[currency] += 1

        merchant = record.get("merchant") or "UNKNOWN"
        by_merchant[merchant] += 1

    return {
        "total_records": len(records),
        "total_events": len(events),
        "failed_events": len(failed),
        "event_types": dict(sorted(event_types.items())),
        "parse_methods": dict(sorted(parse_methods.items())),
        "by_org": dict(sorted(by_org.items())),
        "by_queue": dict(sorted(by_queue.items())),
        "by_currency": dict(sorted(by_currency.items())),
        "top_merchants": dict(
            sorted(by_merchant.items(), key=lambda x: x[1], reverse=True)[:30]
        ),
    }


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ---------------------------------------------------------------------------
# LLens integration -- everything below is new, additive code. Nothing above
# this line was modified from the provided parser.
# ---------------------------------------------------------------------------

DISPLAY_NAME = "OTP Online Processor (SMS/Email XML)"
DEFAULT_SOURCE_SYSTEM = "otp_online_processor"

# This format is not line-delimited -- events are split out of one blob of
# text purely by locating repeated "<M>/<D>/<Y> <H>:<M>:<S> AM/PM" timestamp
# matches (see split_timestamped_events()/TIMESTAMP_RE above), so detect()
# can't match a single anchored per-line regex the way the "Log Tracker No:
# ... =>" family of parsers does. Instead it requires the timestamp shape
# AND at least one OTP-processor-specific marker (mirroring the branches in
# classify_event() above) so a generic "log file with US timestamps" doesn't
# false-positive against this parser.
_DETECT_MARKERS = (
    "msg received--",
    "sms input message",
    "message for queue=",
    "otp processed successfully",
    "force verify by mob",
    "sms placed in queue",
)


def detect(sample_text: str) -> bool:
    text = html.unescape(sample_text)

    if not TIMESTAMP_RE.search(text):
        return False

    lowered = text.lower()
    return any(marker in lowered for marker in _DETECT_MARKERS)


def parse_log_file(log_file_path, output_json_path=None):
    """Adapter for the LLens custom-parser registry. Calls the original,
    unmodified parse_file() above and flattens its two outputs (per-tracker
    merged records + a flat per-event stream, already covering every event
    including unparsed/failed ones) into one list of flat records, which is
    the shape every custom parser in this directory is expected to return
    (see custom_parser_registry.py).

    Design choices:
    - Iterates the flat per-event stream (not the merged per-tracker
      records) so every log entry becomes its own CanonicalLogEvent with
      its own timestamp/line ordering, consistent with how
      parser_AFS_Netcetera.py (the closest existing analog: same
      tracker-based OTP/SMS/email correlation) handles the same
      flat-events-vs-merged-transactions shape.
    - correlation_id is the resolved tracker_no for the event, falling back
      to whatever parse_file() already resolved via pending_tracker for
      events that don't carry their own tracker id (queue/msg-id/otp-
      success/force-verify lines).
    - `details` carries the event's own parsed payload plus a snapshot of
      that tracker's final merged record (once parse_file() has finished
      correlating all events), so downstream views have both the granular
      per-line payload and the aggregated OTP transaction context.
    - Events that failed to parse (whether classified as "other"/unknown or
      that raised during XML extraction) are still emitted as their own
      WARN-level records with the failure reason attached, rather than
      being dropped -- consistent with how every other ingestion path in
      LLens never discards a line.
    """
    records, events, failed = parse_file(log_file_path)

    failure_reason_by_event_no = {f["event_no"]: f["reason"] for f in failed}
    tracker_context = {r["tracker_no"]: r for r in records if r.get("tracker_no")}

    action_labels = {
        "msg_received_sms_xml": "SMS Msg Received (XML)",
        "sms_input_xml": "SMS Input Message (XML)",
        "email_xml": "Email Message (XML)",
        "queue": "Message For Queue",
        "otp_success": "OTP Processed Successfully",
        "sms_queue_msg_id": "SMS Placed In Queue",
        "force_verify_by_mobile": "Force Verify By Mobile",
    }

    out_records = []

    for event in events:
        event_type = event["event_type"]
        tracker = event.get("tracker_no")
        parsed = event.get("parsed")
        failure_reason = failure_reason_by_event_no.get(event["event_no"])

        action = action_labels.get(event_type, event["raw"])
        if tracker and event_type in (
            "msg_received_sms_xml",
            "sms_input_xml",
            "queue",
            "sms_queue_msg_id",
        ):
            action = f"{action} (Tracker={tracker})"
        if failure_reason:
            action = f"{action} -- parse failed: {failure_reason}"

        details = {
            "event_no": event["event_no"],
            "tracker_no": tracker,
            "parsed": parsed,
        }
        if failure_reason:
            details["parse_error"] = failure_reason
        if tracker and tracker in tracker_context:
            details["record"] = tracker_context[tracker]

        out_records.append(
            {
                "timestamp": event["timestamp"],
                "log_level": "WARN" if (failure_reason or event_type == "other") else "INFO",
                "correlation_id": tracker,
                "log_type": event_type,
                "action": action,
                "details": details,
                "_raw_block": f'{event["timestamp_raw"]} : {event["raw"]}',
            }
        )

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(out_records, f, indent=2, default=str)

    return out_records
