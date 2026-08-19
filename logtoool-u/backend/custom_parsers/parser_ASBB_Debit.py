# Adapted from the customer-provided parser_ASBB_Debit.py.
# Parsing logic is UNCHANGED. Only the __main__ block was removed and
# record["_raw_block"] / detect() were added.
#
# See parser_ABCE_Debit.py's header comment for the known ambiguity between
# these two Debit-family parsers -- both detect the same timestamp
# convention, and the ingestion engine breaks ties by field-extraction yield.
import re
import json
import xml.etree.ElementTree as ET

DISPLAY_NAME = "ASBB Debit Portal (Correlation ID + Arrow Timestamp)"
DEFAULT_SOURCE_SYSTEM = "asbb_debit_portal"

_TS_LINE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M\s*->", re.MULTILINE)


def detect(sample_text: str) -> bool:
    return bool(_TS_LINE_RE.search(sample_text))


def parse_log_file(log_file_path, output_json_path=None):
    with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_text = f.read()

    timestamp_pattern = r"^(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)\s+->"
    raw_blocks = re.split(timestamp_pattern, raw_text, flags=re.MULTILINE)

    parsed_records = []

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
        record["_raw_block"] = f"{timestamp} -> {body}"

        corr_match = re.match(r"^(\d+)---(.*)", body, re.DOTALL)
        if corr_match:
            record["correlation_id"] = corr_match.group(1)
            content = corr_match.group(2).strip()
        else:
            content = body

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

        elif "Inputs in " in content:
            record["log_type"] = "FUNCTION_INPUT"
            func_match = re.match(r"Inputs in ([^\(]+)\((.*)\)", content, re.DOTALL)
            if func_match:
                record["action"] = func_match.group(1).strip()
                args_str = func_match.group(2)
                args = {k: v.strip() for k, v in re.findall(r"(\w+)=([^,]*)", args_str)}
                record["details"] = {"parameters": args}

        elif "Warrning" in content or "warning" in content.lower():
            record["log_type"] = "WARNING"
            record["action"] = content

        else:
            record["action"] = content

        parsed_records.append(record)

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as out_file:
            json.dump(parsed_records, out_file, indent=2)
        print(f"Successfully converted '{log_file_path}' to '{output_json_path}'")

    return parsed_records
