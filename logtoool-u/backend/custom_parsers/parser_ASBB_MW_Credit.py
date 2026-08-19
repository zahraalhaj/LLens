# Adapted from the customer-provided parser_ASBB_MW_Credit.py.
# Parsing logic is UNCHANGED. Only the __main__ block was removed and
# record["_raw_block"] / detect() were added (see parser_ABCE_Credit.py
# header comment for why).
#
# Original comment: "needs to parse the customer details from the log file
# and convert it into a structured JSON format including name, address etc."
import re
import json
import xml.etree.ElementTree as ET

DISPLAY_NAME = "ASBB MW Credit Portal (ISO Timestamp + Log Tracker)"
DEFAULT_SOURCE_SYSTEM = "asbb_mw_credit_portal"

# Distinguishes this format from all others in this package: ISO 8601
# timestamp with UTC offset immediately followed by a bracketed level, e.g.
# "2026-08-05 20:17:33.123 +00:00 [INFO]". This is unique enough among the
# 4 custom formats that it can go first in detection order safely.
_TS_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+[+\-]\d{2}:\d{2}\s+\[[A-Z]+\]",
    re.MULTILINE,
)


def detect(sample_text: str) -> bool:
    return bool(_TS_LINE_RE.search(sample_text))


def parse_log_file(log_file_path, output_json_path=None):
    with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_text = f.read()

    # Pattern for ISO timestamps: YYYY-MM-DD HH:MM:SS.mmm +HH:MM [LEVEL]
    # Uses positive lookahead (?=...) to split even if logs are pasted together without newlines
    timestamp_pattern = r"(?=\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+[\+\-]\d{2}:\d{2}\s+\[[A-Z]+\])"
    raw_blocks = re.split(timestamp_pattern, raw_text)

    parsed_records = []

    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue

        # Extract timestamp, log level, and the log message body
        header_match = re.match(
            r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+[\+\-]\d{2}:\d{2})\s+\[([A-Z]+)\]\s+(.*)",
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
            "log_level": log_level,
            "correlation_id": None,
            "log_type": "INFO",
            "action": None,
            "details": {},
        }
        record["_raw_block"] = block

        # Extract "Log Tracker No: <ID> => <content>"
        tracker_match = re.match(
            r"^Log Tracker No:\s*([^\s=>]+)\s*=>\s*(.*)", body, re.DOTALL
        )
        if tracker_match:
            record["correlation_id"] = tracker_match.group(1).strip()
            content = tracker_match.group(2).strip()
        else:
            content = body

        # Case A: XML Payloads
        if "<?xml" in content or "PostXml>" in content:
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
                fields = {
                    field.tag: (field.text or "")
                    for field in root.findall(".//Fields/*")
                }
                msg_type = root.findtext("MsgType")
            except ET.ParseError:
                fields = {}
                for tag, val in re.findall(
                    r"<([A-Za-z0-9_]+)>(.*?)</[A-Za-z0-9_]+>", raw_xml, re.DOTALL
                ):
                    fields[tag] = val.strip()
                msg_type_match = re.search(r"<MsgType>(.*?)</MsgType>", raw_xml)
                msg_type = msg_type_match.group(1) if msg_type_match else None

            record["details"] = {"msg_type": msg_type, "fields": fields}

        # Case B: Function Inputs (e.g., Inputs : CC_ (OrgNo=000,TranReference=00000, CIF=00000))
        elif "Inputs" in content and "(" in content and ")" in content:
            record["log_type"] = "FUNCTION_INPUT"

            func_match = re.search(
                r"Inputs\s*:\s*(\w+)?\s*\((.*?)\)", content, re.DOTALL
            )
            if func_match:
                record["action"] = (
                    func_match.group(1).strip() if func_match.group(1) else "Inputs"
                )
                args_str = func_match.group(2)

                args = {}
                for param in re.finditer(r"(\w+)\s*=\s*([^,]*)", args_str):
                    args[param.group(1).strip()] = param.group(2).strip()

                record["details"] = {"parameters": args}
            else:
                record["action"] = content

        # Case C: Warnings
        elif "Warrning" in content or "warning" in content.lower():
            record["log_type"] = "WARNING"
            record["action"] = content

        # Case D: Generic log entries
        else:
            record["action"] = content

        parsed_records.append(record)

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as out_file:
            json.dump(parsed_records, out_file, indent=2)
        print(f"Successfully converted '{log_file_path}' to '{output_json_path}'")

    return parsed_records
