# needs to parse the customer details from the log file and convert it into a structured JSON format including name, addres etc.

import re
import json
import xml.etree.ElementTree as ET


def parse_log_file(log_file_path, output_json_path=None):
    with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_text = f.read()

    # Pattern for ISO timestamps: YYYY-MM-DD HH:MM:SS.mmm +HH:MM [LEVEL]
    timestamp_pattern = r"(?=\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+[\+\-]\d{2}:\d{2}\s+\[[A-Z]+\])"
    raw_blocks = re.split(timestamp_pattern, raw_text)

    parsed_records = []

    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue

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

        # Case B: Function Inputs
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

        # NEW Case C: Raw Response Payloads (e.g., CC_ListofCardsByCIF mqRetry:0 ...)
        elif (
            "mqRetry:" in content
            or "VP_Response:" in content
            or content.startswith("CC_")
        ):
            record["log_type"] = "RESPONSE_PAYLOAD"

            # Extract main action name (e.g., CC_ListofCardsByCIF)
            action_match = re.match(r"^([A-Za-z0-9_]+)", content)
            record["action"] = action_match.group(1) if action_match else "Response"

            # Parse key header metrics embedded in the response string
            mq_retry = re.search(r"mqRetry:(\d+)", content)
            vp_resp = re.search(r"VP_Response:(\d+)", content)

            record["details"] = {
                "mq_retry": mq_retry.group(1) if mq_retry else None,
                "vp_response": vp_resp.group(1) if vp_resp else None,
                "raw_response_preview": content[:120],  # Store preview to save memory
            }

        # Case D: Warnings
        elif "Warrning" in content or "warning" in content.lower():
            record["log_type"] = "WARNING"
            record["action"] = content

        # Case E: Generic log entries
        else:
            record["action"] = content

        parsed_records.append(record)

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as out_file:
            json.dump(parsed_records, out_file, indent=2)
        print(f"Successfully converted '{log_file_path}' to '{output_json_path}'")

    return parsed_records


if __name__ == "__main__":
    LOG_FILE_PATH = "file.log"
    JSON_OUTPUT_PATH = "output.json"

    data = parse_log_file(LOG_FILE_PATH, JSON_OUTPUT_PATH)
