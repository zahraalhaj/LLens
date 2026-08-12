# Adapted from the customer-provided parser_ABCE_Debit.py.
# Parsing logic is UNCHANGED. Only the __main__ block was removed and
# record["_raw_block"] / detect() were added (see parser_ABCE_Credit.py
# header comment for why).
#
# NOTE ON AMBIGUITY: this format's timestamp+delimiter convention
# ("M/D/YYYY H:MM:SS AM/PM ->") is structurally identical to
# parser_ASBB_Debit.py's. There is no reliable content-based signal in the
# sample alone that tells the two apart with certainty -- ABCE_Debit is a
# superset (tolerates malformed correlation-ID prefixes and an extra
# "New Request" step-event case) but that doesn't make it detectable as
# distinct from a well-formed ASBB_Debit file. The ingestion engine breaks
# ties between the two by running both and keeping whichever extracts more
# non-empty fields on the sample; if you know which vendor a file came
# from, force it explicitly via the profile picker in Upload rather than
# relying on auto-detection.
import re
import json
import xml.etree.ElementTree as ET

DISPLAY_NAME = "ABCE Debit Portal (Correlation ID + Arrow Timestamp)"
DEFAULT_SOURCE_SYSTEM = "abce_debit_portal"

_TS_LINE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M\s*->", re.MULTILINE)


def detect(sample_text: str) -> bool:
    return bool(_TS_LINE_RE.search(sample_text))


def parse_log_file(log_file_path, output_json_path=None):
    with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_text = f.read()

    # Match timestamp pattern: date + time + AM/PM ->
    timestamp_pattern = r"^(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)\s+->"
    raw_blocks = re.split(timestamp_pattern, raw_text, flags=re.MULTILINE)

    parsed_records = []

    for i in range(1, len(raw_blocks), 2):
        timestamp = raw_blocks[i].strip()
        body = raw_blocks[i + 1].strip()

        # Skip divider line entries
        if re.match(r"^-+$", body):
            continue

        record = {
            "timestamp": timestamp,
            "correlation_id": None,
            "log_type": "INFO",
            "action": None,
            "details": {},
        }
        record["_raw_block"] = f"{timestamp} -> {body}"

        # Extract Correlation ID (handles standard dashes ---, em-dashes —, and malformed prefixes like 0—00000000)
        corr_match = re.match(r"^([\d—-]+?)(?:---|[—])\s*(.*)", body, re.DOTALL)
        if corr_match:
            record["correlation_id"] = (
                corr_match.group(1).replace("—", "").replace("-", "").strip() or None
            )
            content = corr_match.group(2).strip()
        else:
            content = body

        # Case A: XML Payloads (handles both clean and broken XML structure)
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

            # Try standard XML parsing first; fall back to Regex extraction if tags are broken
            try:
                root = ET.fromstring(raw_xml)
                fields = {
                    field.tag: (field.text or "")
                    for field in root.findall(".//Fields/*")
                }
                msg_type = root.findtext("MsgType")
            except ET.ParseError:
                # Robust regex fallback for malformed/mismatched tags
                fields = {}
                for tag, val in re.findall(
                    r"<([A-Za-z0-9_]+)>(.*?)</[A-Za-z0-9_]+>", raw_xml, re.DOTALL
                ):
                    fields[tag] = val.strip()

                msg_type_match = re.search(r"<MsgType>(.*?)</MsgType>", raw_xml)
                msg_type = msg_type_match.group(1) if msg_type_match else None

            record["details"] = {
                "msg_type": msg_type,
                "fields": fields,
            }

        # Case B: Function Inputs (e.g., Inputs in GetCardListByCustomerId(...))
        elif "Inputs in " in content:
            record["log_type"] = "FUNCTION_INPUT"
            func_match = re.search(r"Inputs in ([^\(]+)\((.*)\)", content, re.DOTALL)
            if func_match:
                record["action"] = func_match.group(1).strip()
                args_str = func_match.group(2)

                args = {}
                for param in re.finditer(r"(\w+)\s*=\s*([^,]*)", args_str):
                    args[param.group(1).strip()] = param.group(2).strip()

                record["details"] = {"parameters": args}

        # Case C: Warnings
        elif "Warrning" in content or "warning" in content.lower():
            record["log_type"] = "WARNING"
            record["action"] = content

        # Case D: Step / Pipeline Requests
        elif "New Request" in content:
            record["log_type"] = "STEP_EVENT"
            record["action"] = content

        # Case E: Generic log messages
        else:
            record["action"] = content

        parsed_records.append(record)

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as out_file:
            json.dump(parsed_records, out_file, indent=2)
        print(f"Successfully converted '{log_file_path}' to '{output_json_path}'")

    return parsed_records
