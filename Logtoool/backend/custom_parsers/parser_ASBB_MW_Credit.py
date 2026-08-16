import re
import json
import xml.etree.ElementTree as ET
from datetime import datetime

DISPLAY_NAME = "ASBB MW Credit Portal (ISO Timestamp + Log Tracker)"
DEFAULT_SOURCE_SYSTEM = "asbb_mw_credit_portal"

_TS_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}\s+"
    r"\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+"
    r"[+\-]\d{2}:\d{2}\s+\[[A-Z]+\]",
    re.MULTILINE,
)


def detect(sample_text: str) -> bool:
    return bool(_TS_LINE_RE.search(sample_text))


def normalize_timestamp(timestamp):
    try:
        return datetime.fromisoformat(timestamp).isoformat()
    except Exception:
        return timestamp


def parse_log_file(
    log_file_path,
    output_json_path=None,
):

    with open(
        log_file_path,
        "r",
        encoding="utf-8",
        errors="ignore",
    ) as f:
        raw_text = f.read()

    block_pattern = (
        r"(?="
        r"\d{4}-\d{2}-\d{2}\s+"
        r"\d{2}:\d{2}:\d{2}"
        r"(?:\.\d+)?\s+"
        r"[+\-]\d{2}:\d{2}\s+"
        r"\[[A-Z]+\]"
        r")"
    )

    blocks = [
        b.strip()
        for b in re.split(
            block_pattern,
            raw_text,
        )
        if b.strip()
    ]

    parsed_records = []

    for block in blocks:

        header_match = re.match(
            r"^("
            r"\d{4}-\d{2}-\d{2}\s+"
            r"\d{2}:\d{2}:\d{2}"
            r"(?:\.\d+)?\s+"
            r"[+\-]\d{2}:\d{2}"
            r")\s+"
            r"\[([A-Z]+)\]\s+"
            r"(.*)",
            block,
            re.DOTALL,
        )

        if not header_match:
            continue

        timestamp = header_match.group(1).strip()
        log_level = header_match.group(2).strip()
        body = header_match.group(3).strip()

        record = {
            "timestamp": timestamp,
            "timestamp_iso": normalize_timestamp(timestamp),
            "log_level": log_level,
            "correlation_id": None,
            "log_type": "GENERIC",
            "action": None,
            "retry_count": None,
            "details": {},
            "raw_message": body,
        }

        # ------------------------------------------
        # Log Tracker extraction
        # ------------------------------------------
        tracker_match = re.match(
            r"^Log Tracker No:\s*([^\s=>]+)" r"\s*=>\s*(.*)",
            body,
            re.DOTALL,
        )

        if tracker_match:

            record["correlation_id"] = tracker_match.group(1).strip()

            content = tracker_match.group(2).strip()

        else:

            content = body

        # ------------------------------------------
        # mqRetry extraction
        # ------------------------------------------
        retry_match = re.search(
            r"mqRetry\s*:?\s*(\d+)",
            content,
            re.IGNORECASE,
        )

        if retry_match:
            record["retry_count"] = int(retry_match.group(1))

        # ------------------------------------------
        # XML Payload
        # ------------------------------------------
        if "<?xml" in content or "PostXml>" in content or "<Iso" in content:

            record["log_type"] = "XML_PAYLOAD"

            xml_start = content.find("<?xml")

            if xml_start == -1:
                xml_start = content.find("<Iso")

            record["action"] = (
                content[:xml_start].strip(" :-")
                if xml_start != -1
                else "XML API Request"
            )

            raw_xml = content[xml_start:] if xml_start != -1 else content

            try:

                root = ET.fromstring(raw_xml)

                fields = {}

                for field in root.findall(".//Fields/*"):
                    fields[field.tag] = field.text or ""

                msg_type = root.findtext("MsgType")

            except ET.ParseError:

                fields = {}

                for tag, val in re.findall(
                    r"<([A-Za-z0-9_]+)>" r"(.*?)" r"</[A-Za-z0-9_]+>",
                    raw_xml,
                    re.DOTALL,
                ):
                    fields[tag] = val.strip()

                msg_type_match = re.search(
                    r"<MsgType>(.*?)</MsgType>",
                    raw_xml,
                    re.DOTALL,
                )

                msg_type = msg_type_match.group(1) if msg_type_match else None

            record["details"] = {
                "msg_type": msg_type,
                "fields": fields,
                "raw_xml": raw_xml,
            }

        # ------------------------------------------
        # Function Input
        # ------------------------------------------
        elif "Inputs" in content and "(" in content and ")" in content:

            record["log_type"] = "FUNCTION_INPUT"

            func_match = re.search(
                r"Inputs\s*:\s*" r"([A-Za-z0-9_]+)?" r"\s*\((.*?)\)",
                content,
                re.DOTALL,
            )

            if func_match:

                record["action"] = (
                    func_match.group(1).strip() if func_match.group(1) else "Inputs"
                )

                args = {}

                for param in re.finditer(
                    r"(\w+)\s*=\s*([^,]*)",
                    func_match.group(2),
                ):
                    args[param.group(1).strip()] = param.group(2).strip()

                record["details"] = {"parameters": args}

            else:

                record["action"] = content

        # ------------------------------------------
        # Function Response
        # ------------------------------------------
        elif "VP_Response" in content or "Response:" in content:

            record["log_type"] = "FUNCTION_RESPONSE"

            action_match = re.match(
                r"([A-Za-z0-9_]+)",
                content,
            )

            record["action"] = action_match.group(1) if action_match else "Response"

            record["details"] = {"response": content}

        # ------------------------------------------
        # Warning
        # ------------------------------------------
        elif "Warrning" in content or "warning" in content.lower():

            record["log_type"] = "WARNING"
            record["action"] = content

        # ------------------------------------------
        # Error
        # ------------------------------------------
        elif any(
            word in content.lower()
            for word in [
                "exception",
                "error",
                "failed",
                "timeout",
                "fault",
            ]
        ):

            record["log_type"] = "ERROR"
            record["action"] = content

        # ------------------------------------------
        # Generic
        # ------------------------------------------
        else:

            action_match = re.match(
                r"([A-Za-z0-9_]+)",
                content,
            )

            if action_match:
                record["action"] = action_match.group(1)
            else:
                record["action"] = content

        parsed_records.append(record)

    if output_json_path:

        with open(
            output_json_path,
            "w",
            encoding="utf-8",
        ) as out_file:

            json.dump(
                parsed_records,
                out_file,
                indent=2,
                ensure_ascii=False,
            )

        print(
            f"Successfully converted " f"'{log_file_path}' " f"to '{output_json_path}'"
        )

    return parsed_records
