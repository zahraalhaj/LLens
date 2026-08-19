# Adapted from the customer-provided parser_ABCE_Credit.py.
# Parsing logic is UNCHANGED from the original. Two additive-only changes:
#   1. Removed the __main__ runner block (this now runs as a library module).
#   2. record["_raw_block"] captures the original source text per record,
#      purely for the app's raw-log inspection view -- does not affect any
#      of the original field extraction logic.
#
# Original comment: "this has been used TO ASBB_MWCreditPortal.log file to
# parse and convert to JSON format"
import re
import json
import xml.etree.ElementTree as ET

DISPLAY_NAME = "ABCE Credit Portal (Log Tracker / ISO8583 XML)"
DEFAULT_SOURCE_SYSTEM = "abce_credit_portal"

# Distinguishes this format from the others in this package: US-style
# M/D/YYYY H:MM:SS AM/PM timestamp with NO "->" after it (that's the Debit
# family), typically followed somewhere by "Log Tracker No:".
_TS_LINE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M(?!\s*->)", re.MULTILINE)


def detect(sample_text: str) -> bool:
    return bool(_TS_LINE_RE.search(sample_text)) and "Log Tracker No:" in sample_text


def parse_log_file(log_file_path, output_json_path=None):
    with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_text = f.read()

    # Match timestamp pattern followed by "=>"
    timestamp_pattern = r"^(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)\s+"
    raw_blocks = re.split(timestamp_pattern, raw_text, flags=re.MULTILINE)

    parsed_records = []

    # re.split creates pairs: [leading_text, timestamp_1, body_1, timestamp_2, body_2, ...]
    for i in range(1, len(raw_blocks), 2):
        timestamp = raw_blocks[i].strip()
        body = raw_blocks[i + 1].strip()

        record = {
            "timestamp": timestamp,
            "correlation_id": None,
            "log_type": "INFO",
            "action": None,
            "details": {},
        }
        record["_raw_block"] = f"{timestamp} {body}"

        # Extract "Log Tracker No: <ID> => <content>"
        tracker_match = re.match(
            r"^Log Tracker No:\s*([^\s=>]+)\s*=>\s*(.*)", body, re.DOTALL
        )
        if tracker_match:
            record["correlation_id"] = tracker_match.group(1).strip()
            content = tracker_match.group(2).strip()
        else:
            content = body

        # Case A: Handle XML Payloads
        if "<?xml" in content or "<Iso8583PostXml>" in content:
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

        # Case B: Handle Function Inputs (e.g., CC_AccountEnquiry Inputs-:Data in CC_AccountEnquiry(...))
        elif "Inputs" in content and "(" in content and ")" in content:
            record["log_type"] = "FUNCTION_INPUT"

            # Extract function name and arguments inside parenthesis
            func_match = re.search(
                r"(?:Inputs(?:-:\s*Data\s+in)?\s+)?(\w+)\s*\((.*?)\)",
                content,
                re.DOTALL,
            )
            if func_match:
                record["action"] = func_match.group(1).strip()
                args_str = func_match.group(2)

                # Extract key=value parameters cleanly
                args = {}
                for param in re.finditer(r"(\w+)\s*=\s*([^,]*)", args_str):
                    args[param.group(1).strip()] = param.group(2).strip()

                record["details"] = {"parameters": args}
            else:
                record["action"] = content

        # Case C: Handle Warnings
        elif "Warrning" in content or "warning" in content.lower():
            record["log_type"] = "WARNING"
            record["action"] = content

        # Case D: Generic Log Messages
        else:
            record["action"] = content

        parsed_records.append(record)

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as out_file:
            json.dump(parsed_records, out_file, indent=2)
        print(f"Successfully converted '{log_file_path}' to '{output_json_path}'")

    return parsed_records
