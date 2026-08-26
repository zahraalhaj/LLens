import glob
import json
import os
import re
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest


def parse_log_file(log_file_path):
    print(f"Parsing: {log_file_path}")
    with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_text = f.read()

    # Detect ISO timestamps vs Legacy timestamps dynamically
    if re.search(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", raw_text):
        timestamp_pattern = r"(?=\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+[\+\-]\d{2}:\d{2}\s+\[[A-Z]+\])"
        raw_blocks = re.split(timestamp_pattern, raw_text)
        is_iso = True
    else:
        timestamp_pattern = (
            r"^(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)\s+(?:->|=>)?"
        )
        raw_blocks = re.split(timestamp_pattern, raw_text, flags=re.MULTILINE)
        is_iso = False

    parsed_records = []
    file_name = os.path.basename(log_file_path)

    if is_iso:
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

            timestamp, log_level, body = header_match.groups()
            process_record(
                timestamp.strip(),
                log_level.strip(),
                body.strip(),
                parsed_records,
                file_name,
            )
    else:
        for i in range(1, len(raw_blocks), 2):
            timestamp = raw_blocks[i].strip()
            body = raw_blocks[i + 1].strip()
            if re.match(r"^-+$", body):
                continue
            process_record(timestamp, "INFO", body, parsed_records, file_name)

    return parsed_records   


def process_record(timestamp, log_level, body, parsed_records, file_name):
    record = {
        "source_file": file_name,
        "timestamp": timestamp,
        "log_level": log_level,
        "correlation_id": None,
        "log_type": "INFO",
        "action": None,
        "details": {},
    }

    # Extract Correlation ID / Tracker Number
    tracker_match = re.search(
        r"(?:Log Tracker No:\s*([^\s=>]+)|([\d—-]+?)(?:---|—))\s*(?:=>)?\s*(.*)",
        body,
        re.DOTALL,
    )
    if tracker_match:
        cid = tracker_match.group(1) or tracker_match.group(2)
        record["correlation_id"] = (
            cid.replace("—", "").replace("-", "").strip() if cid else None
        )
        content = tracker_match.group(3).strip()
    else:
        content = body

    # Categorize Log Type
    if "<?xml" in content or "PostXml>" in content:
        record["log_type"] = "XML_PAYLOAD"
        xml_start = (
            content.find("<?xml") if "<?xml" in content else content.find("<Iso")
        )
        record["action"] = (
            content[:xml_start].strip(" :-") if xml_start != -1 else "XML API Request"
        )
        raw_xml = content[xml_start:] if xml_start != -1 else content

        try:
            root = ET.fromstring(raw_xml)
            fields = {
                field.tag: (field.text or "") for field in root.findall(".//Fields/*")
            }
            msg_type = root.findtext("MsgType")
        except ET.ParseError:
            fields = {
                tag: val.strip()
                for tag, val in re.findall(
                    r"<([A-Za-z0-9_]+)>(.*?)</[A-Za-z0-9_]+>", raw_xml, re.DOTALL
                )
            }
            msg_type_match = re.search(r"<MsgType>(.*?)</MsgType>", raw_xml)
            msg_type = msg_type_match.group(1) if msg_type_match else None

        record["details"] = {"msg_type": msg_type, "fields": fields}

    elif "Inputs" in content and "(" in content and ")" in content:
        record["log_type"] = "FUNCTION_INPUT"
        func_match = re.search(
            r"(?:Inputs\s*:?\s*-?\s*Data\s+in\s+)?(\w+)?\s*\((.*?)\)",
            content,
            re.DOTALL,
        )
        if func_match:
            record["action"] = (
                func_match.group(1).strip() if func_match.group(1) else "Inputs"
            )
            args = {
                m.group(1).strip(): m.group(2).strip()
                for m in re.finditer(r"(\w+)\s*=\s*([^,]*)", func_match.group(2))
            }
            record["details"] = {"parameters": args}
        else:
            record["action"] = content

    elif (
        "mqRetry:" in content or "VP_Response:" in content or content.startswith("CC_")
    ):
        record["log_type"] = "RESPONSE_PAYLOAD"
        action_match = re.match(r"^([A-Za-z0-9_]+)", content)
        record["action"] = action_match.group(1) if action_match else "Response"
        mq_retry = re.search(r"mqRetry:(\d+)", content)
        vp_resp = re.search(r"VP_Response:(\d+)", content)
        record["details"] = {
            "mq_retry": mq_retry.group(1) if mq_retry else None,
            "vp_response": vp_resp.group(1) if vp_resp else None,
        }

    elif "Warrning" in content or "warning" in content.lower():
        record["log_type"] = "WARNING"
        record["action"] = content

    else:
        record["action"] = content

    parsed_records.append(record)


# STEP 2: PROFILING & MACHINE LEARNING ANOMALY DETECTION
def profile_and_detect_anomalies(parsed_data):
    if not parsed_data:
        print("No valid log records found across directory files.")
        return

    df = pd.json_normalize(parsed_data)
    df["timestamp_dt"] = pd.to_datetime(
        df["timestamp"], format="mixed", errors="coerce", utc=True
    )

    df = df.dropna(subset=["timestamp_dt"])
    df = df.sort_values("timestamp_dt").reset_index(drop=True)

    print("\n==================================================")
    print("        CONSOLIDATED LOG PROFILING SUMMARY        ")
    print("==================================================")
    print(f"Total Logs Parsed : {len(df)}")
    print(f"Files Processed   : {df['source_file'].nunique()}")
    print(
        f"Time Range        : {df['timestamp_dt'].min()} to {df['timestamp_dt'].max()}"
    )
    print("\nLogs per File:\n", df["source_file"].value_counts().to_string())
    print("\nLog Type Counts:\n", df["log_type"].value_counts().to_string())

    # 1-Minute Resampled Feature Matrix
    time_series = (
        df.set_index("timestamp_dt")
        .groupby(pd.Grouper(freq="1min"))
        .agg(
            total_logs=("log_type", "count"),
            warnings=("log_type", lambda x: (x == "WARNING").sum()),
            xml_payloads=("log_type", lambda x: (x == "XML_PAYLOAD").sum()),
            function_inputs=("log_type", lambda x: (x == "FUNCTION_INPUT").sum()),
            responses=("log_type", lambda x: (x == "RESPONSE_PAYLOAD").sum()),
            unique_correlations=("correlation_id", "nunique"),
        )
        .reset_index()
    )

    time_series["warning_ratio"] = (
        time_series["warnings"] / time_series["total_logs"]
    ).fillna(0)
    time_series["hour"] = time_series["timestamp_dt"].dt.hour
    time_series["sin_hour"] = np.sin(2 * np.pi * time_series["hour"] / 24.0)
    time_series["cos_hour"] = np.cos(2 * np.pi * time_series["hour"] / 24.0)

    # ML Feature Set
    feature_cols = [
        "total_logs",
        "warnings",
        "warning_ratio",
        "unique_correlations",
        "sin_hour",
        "cos_hour",
    ]
    X = time_series[feature_cols].copy()

    # Isolation Forest Anomaly Detection Model
    model = IsolationForest(contamination=0.05, random_state=42)
    time_series["anomaly_score"] = model.fit_predict(X)

    # -1 indicates an anomaly detected by Isolation Forest
    anomalies = time_series[time_series["anomaly_score"] == -1]

    print("\n==================================================")
    print("        MACHINE LEARNING ANOMALY RESULTS          ")
    print("==================================================")
    print(f"Anomalous Time Windows Flagged: {len(anomalies)}")
    if not anomalies.empty:
        print(
            anomalies[
                ["timestamp_dt", "total_logs", "warnings", "warning_ratio"]
            ].to_string(index=False)
        )

    # Export Results
    time_series.to_csv("profile_ml_anomalies.csv", index=False)
    print(
        "\n[✔] Profiling complete. ML anomaly matrix saved to 'profile_ml_anomalies.csv'."
    )


# =====================================================================
# RUN PIPELINE ACROSS CURRENT DIRECTORY
# =====================================================================
if __name__ == "__main__":
    current_directory = os.getcwd()
    log_files = glob.glob(os.path.join(current_directory, "*.log"))

    print(f"Found {len(log_files)} '.log' files in directory: {current_directory}\n")

    all_parsed_logs = []
    for file in log_files:
        all_parsed_logs.extend(parse_log_file(file))

    # Save Consolidated JSON
    output_json = "all_parsed_logs.json"
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(all_parsed_logs, f, indent=2)
    print(f"\n[✔] Consolidated {len(all_parsed_logs)} records into '{output_json}'")

    # Run ML Profiler on All Log Data
    profile_and_detect_anomalies(all_parsed_logs)
