import re
import json
import xml.etree.ElementTree as ET
from datetime import datetime

DISPLAY_NAME = "ABCE Credit Portal (Log Tracker / ISO8583 XML)"
DEFAULT_SOURCE_SYSTEM = "abce_credit_portal"

# Timestamp format used by ABCE logs
_TIMESTAMP_RE = r"\d{1,2}/\d{1,2}/\d{4}\s+" r"\d{1,2}:\d{2}:\d{2}\s+[AP]M"

# Detect parser
_TS_LINE_RE = re.compile(
    rf"^{_TIMESTAMP_RE}(?!\s*->)",
    re.MULTILINE,
)


def detect(sample_text: str) -> bool:
    return bool(_TS_LINE_RE.search(sample_text)) and ("Log Tracker No:" in sample_text)


def normalize_timestamp(ts):
    try:
        return datetime.strptime(ts, "%m/%d/%Y %I:%M:%S %p").isoformat()
    except Exception:
        return ts


def mask_sensitive(value):
    if not value:
        return value

    value = str(value)

    if len(value) <= 8:
        return value

    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def parse_function_parameters(args_str):
    params = {}

    for match in re.finditer(r"(\w+)\s*=\s*([^,]*)", args_str):
        key = match.group(1).strip()
        val = match.group(2).strip()

        if key.lower() in (
            "pcinumber",
            "cif",
            "email",
            "user",
            "psw",
        ):
            val = mask_sensitive(val)

        params[key] = val

    return params


def parse_log_file(
    log_file_path,
    output_json_path=None,
    group_by_correlation=False,
):

    with open(
        log_file_path,
        "r",
        encoding="utf-8",
        errors="ignore",
    ) as f:
        raw_text = f.read()

    # Split whenever a timestamp starts
    block_pattern = rf"(?=^{_TIMESTAMP_RE}\s+Log Tracker No:)"

    blocks = [
        b.strip()
        for b in re.split(
            block_pattern,
            raw_text,
            flags=re.MULTILINE,
        )
        if b.strip()
    ]

    parsed_records = []

    for block in blocks:

        ts_match = re.match(
            rf"^({_TIMESTAMP_RE})",
            block,
        )

        if not ts_match:
            continue

        timestamp = ts_match.group(1)

        body = block[len(timestamp) :].strip()

        record = {
            "timestamp": timestamp,
            "timestamp_iso": normalize_timestamp(timestamp),
            "correlation_id": None,
            "log_type": "GENERIC",
            "action": None,
            "retry_count": None,
            "calling_method": None,
            "details": {},
            "raw_message": body,
        }

        tracker_match = re.match(
            r"Log Tracker No:\s*([^\s=]+)\s*=>\s*(.*)",
            body,
            re.DOTALL,
        )

        if tracker_match:
            record["correlation_id"] = tracker_match.group(1).strip()
            content = tracker_match.group(2).strip()
        else:
            content = body

        retry_match = re.search(
            r"mqRetry\s*:\s*(\d+)|mqRetry\s*(\d+)",
            content,
        )

        if retry_match:
            retry_val = retry_match.group(1) or retry_match.group(2)

            record["retry_count"] = int(retry_val)

        calling_match = re.search(
            r"Calling Method\s*:\s*\{([^}]*)\}",
            content,
        )

        if calling_match:
            record["calling_method"] = calling_match.group(1).strip()

        # --------------------------------------------------
        # XML Payload
        # --------------------------------------------------
        if "<?xml" in content or "<Iso8583PostXml" in content:
            record["log_type"] = "XML_PAYLOAD"

            xml_start = content.find("<?xml")

            if xml_start == -1:
                xml_start = content.find("<Iso8583PostXml")

            record["action"] = content[:xml_start].strip(" :-")

            raw_xml = content[xml_start:]

            try:
                root = ET.fromstring(raw_xml)

                fields = {}

                for field in root.findall(".//Fields/*"):
                    fields[field.tag] = field.text or ""

                record["details"] = {
                    "msg_type": root.findtext("MsgType"),
                    "fields": fields,
                }

            except ET.ParseError:

                record["details"] = {"raw_xml": raw_xml}

        # --------------------------------------------------
        # Function Input
        # --------------------------------------------------
        elif "Inputs" in content and "(" in content and ")" in content:

            record["log_type"] = "FUNCTION_INPUT"

            func_match = re.search(
                r"([A-Za-z0-9_]+)\s*\((.*?)\)",
                content,
                re.DOTALL,
            )

            if func_match:

                record["action"] = func_match.group(1)

                record["details"] = {
                    "parameters": parse_function_parameters(func_match.group(2))
                }

        # --------------------------------------------------
        # VP Response
        # --------------------------------------------------
        elif "VP_Response:" in content:

            record["log_type"] = "FUNCTION_RESPONSE"

            action_match = re.match(
                r"([A-Za-z0-9_]+)",
                content,
            )

            if action_match:
                record["action"] = action_match.group(1)

            parts = content.split(
                "VP_Response:",
                1,
            )

            record["details"] = {
                "response": (parts[1].strip() if len(parts) > 1 else None)
            }

        # --------------------------------------------------
        # Request End
        # --------------------------------------------------
        elif re.search(
            r"End of request",
            content,
            re.IGNORECASE,
        ):

            record["log_type"] = "REQUEST_END"

            end_match = re.match(
                r"([A-Za-z0-9_]+)",
                content,
            )

            if end_match:
                record["action"] = end_match.group(1)

        # --------------------------------------------------
        # Warning
        # --------------------------------------------------
        elif "Warrning" in content or "warning" in content.lower():

            record["log_type"] = "WARNING"
            record["action"] = content

        # --------------------------------------------------
        # Generic
        # --------------------------------------------------
        else:

            record["log_type"] = "GENERIC"
            record["action"] = content

        parsed_records.append(record)

    # --------------------------------------------------
    # Optional transaction grouping
    # --------------------------------------------------
    if group_by_correlation:

        grouped = {}

        for rec in parsed_records:

            cid = rec["correlation_id"] or "NO_CORRELATION_ID"

            grouped.setdefault(cid, []).append(rec)

        result = []

        for cid, events in grouped.items():

            result.append(
                {
                    "correlation_id": cid,
                    "event_count": len(events),
                    "events": events,
                }
            )

    else:

        result = parsed_records

    if output_json_path:

        with open(
            output_json_path,
            "w",
            encoding="utf-8",
        ) as out_file:

            json.dump(
                result,
                out_file,
                indent=2,
                ensure_ascii=False,
            )

        print(
            f"Successfully converted "
            f"'{log_file_path}' "
            f"to "
            f"'{output_json_path}'"
        )

    return result
