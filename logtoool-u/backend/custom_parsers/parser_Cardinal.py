import re
import json
import html
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from backend.core.currency import resolve_transaction_currency

# ============================================================
# Core patterns
# ============================================================

TIMESTAMP_PATTERN = r"\d{1,2}/\d{1,2}/\d{4}\s+" r"\d{1,2}:\d{2}:\d{2}\s+" r"(?:AM|PM)"

EVENT_START_RE = re.compile(
    rf"^(?P<timestamp>{TIMESTAMP_PATTERN})\s+"
    r"Log Tracker No:\s*"
    r"(?P<tracker>[A-Z]{2}\d+)\s*"
    r"(?:=>|=\\>|=&gt;)\s*"
    r"(?P<message>.*)$",
    re.IGNORECASE,
)

TIMESTAMP_ANYWHERE_RE = re.compile(
    rf"(?P<timestamp>{TIMESTAMP_PATTERN})\s+"
    r"Log Tracker No:\s*"
    r"(?P<tracker>[A-Z]{2}\d+)\s*"
    r"(?:=>|=\\>|=&gt;)\s*",
    re.IGNORECASE,
)

OOB_TRACKER_RE = re.compile(
    r"OOB Tracker ID:\s*(?P<oob_tracker_id>[A-Za-z0-9\-]+)", re.IGNORECASE
)

HTTP_STATUS_RE = re.compile(r"\bHTTP\s+(?P<http_status>\d{3})\b", re.IGNORECASE)

ERROR_CODE_RE = re.compile(
    r"\bError code:\s*(?P<error_code>[A-Za-z0-9_\-]+)", re.IGNORECASE
)

URL_RE = re.compile(
    r"(?P<url>/security/out-of-band/[^\s\"']+|https?://[^\s\"']+)", re.IGNORECASE
)

STEPUP_ID_PATH_RE = re.compile(
    r"/(?:status|process/status)/" r"(?P<stepup_id>[0-9a-fA-F\-]{36})"
)

UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}\b"
)

MOBILE_RE = re.compile(r"\+\d{7,15}")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@" r"[A-Za-z0-9.\-]+\." r"[A-Za-z]{2,}")

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

STACK_FRAME_RE = re.compile(
    r"^\s*at\s+"
    r"(?P<method>.+?)"
    r"(?:\s+in\s+(?P<file>.+?):line\s+(?P<line>\d+))?\s*$"
)

RESPONSE_CODE_RE = re.compile(
    r"\bResponseCode\s*[=:]\s*(?P<response_code>\d+)", re.IGNORECASE
)

BANK_ORG_RE = re.compile(
    r"\bBankOrg\s*[=:]\s*(?P<bank_org>[A-Za-z0-9]+)", re.IGNORECASE
)

CUSTOMER_ID_RE = re.compile(
    r"\bCustomerId\s*[=:]\s*(?P<customer_id>[A-Za-z0-9\-]+)", re.IGNORECASE
)

ENABLE_OOB_RE = re.compile(r"\bEnableOOB\s*[=:]\s*(?P<enable_oob>[YN])", re.IGNORECASE)

CARD_BLOCKED_RE = re.compile(
    r"\bcardBlocked\s*[=:]\s*(?P<card_blocked>true|false)", re.IGNORECASE
)

STATUS_ENUM_RE = re.compile(
    r"\bstatusResponseEnum\s*[=:]\s*(?P<status>[A-Z_]+)", re.IGNORECASE
)

QUEUE_RE = re.compile(r"\bAFS\.[A-Za-z0-9._\-]+")

MSG_ID_RE = re.compile(r"\bMsgId:\s*(?P<msg_id>[A-Za-z0-9\-]+)", re.IGNORECASE)


# ============================================================
# General helpers
# ============================================================


def clean_text(value):
    if value is None:
        return ""

    return (
        str(value).replace("\x00", "").replace("﻿", "").replace("\\>", ">").strip()
    )


def parse_timestamp(value):
    try:
        return datetime.strptime(value, "%m/%d/%Y %I:%M:%S %p").isoformat(sep=" ")
    except Exception:
        return value


def unique(values):
    result = []
    seen = set()

    for value in values:
        if value in (None, ""):
            continue

        if value not in seen:
            seen.add(value)
            result.append(value)

    return result


def match_value(pattern, text, group):
    match = pattern.search(text)

    if not match:
        return None

    return clean_text(match.group(group))


def to_bool(value):
    if value is None:
        return None

    return str(value).lower() == "true"


# ============================================================
# Logical event streaming and multiline support
# ============================================================


def iter_logical_events(file_path):
    """
    Streams the file and groups multiline exception/stack-trace lines
    under the preceding timestamped event.
    """

    current = None
    orphan_no = 0

    with Path(file_path).open("r", encoding="utf-8", errors="replace") as file:
        for physical_line_no, line in enumerate(file, start=1):
            line = clean_text(line)

            if not line:
                continue

            match = EVENT_START_RE.match(line)

            if match:
                if current:
                    yield current

                current = {
                    "physical_line_start": physical_line_no,
                    "physical_line_end": physical_line_no,
                    "timestamp_raw": match.group("timestamp"),
                    "tracker_no": match.group("tracker"),
                    "message_lines": [clean_text(match.group("message"))],
                }

            elif current:
                current["physical_line_end"] = physical_line_no
                current["message_lines"].append(line)

            else:
                orphan_no += 1

                yield {
                    "physical_line_start": physical_line_no,
                    "physical_line_end": physical_line_no,
                    "timestamp_raw": None,
                    "tracker_no": None,
                    "message_lines": [line],
                    "orphan_no": orphan_no,
                }

        if current:
            yield current


def split_compacted_events(file_path):
    """
    Fallback for a file containing many events in one physical line.
    """

    content = Path(file_path).read_text(encoding="utf-8", errors="replace")

    content = clean_text(content)
    matches = list(TIMESTAMP_ANYWHERE_RE.finditer(content))

    events = []

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)

        events.append(
            {
                "physical_line_start": None,
                "physical_line_end": None,
                "timestamp_raw": match.group("timestamp"),
                "tracker_no": match.group("tracker"),
                "message_lines": [clean_text(content[start:end])],
            }
        )

    return events


def load_logical_events(file_path):
    events = list(iter_logical_events(file_path))

    timestamped = sum(1 for event in events if event.get("timestamp_raw"))

    # If the file contains timestamp markers but line-based parsing
    # found almost none, use the compacted-file parser.
    content_head = Path(file_path).read_text(encoding="utf-8", errors="replace")[:50000]

    markers_in_head = len(TIMESTAMP_ANYWHERE_RE.findall(content_head))

    if timestamped == 0 and markers_in_head > 0:
        return split_compacted_events(file_path)

    return events


# ============================================================
# Balanced JSON extraction
# ============================================================


def extract_json_objects(text):
    """
    Extracts all balanced JSON objects from a message.
    Supports nested arrays and objects.
    """

    objects = []
    index = 0

    while index < len(text):
        start = text.find("{", index)

        if start == -1:
            break

        depth = 0
        in_string = False
        escape = False
        completed = False

        for position in range(start, len(text)):
            character = text[position]

            if escape:
                escape = False
                continue

            if character == "\\":
                escape = True
                continue

            if character == '"':
                in_string = not in_string
                continue

            if not in_string:
                if character == "{":
                    depth += 1

                elif character == "}":
                    depth -= 1

                    if depth == 0:
                        raw_json = text[start : position + 1]

                        try:
                            parsed_json = json.loads(raw_json)

                            objects.append(
                                {
                                    "status": "parsed",
                                    "raw": raw_json,
                                    "value": parsed_json,
                                }
                            )

                        except Exception as exception:
                            objects.append(
                                {
                                    "status": "invalid_json",
                                    "raw": raw_json,
                                    "error": str(exception),
                                    "value": regex_json_fallback(raw_json),
                                }
                            )

                        index = position + 1
                        completed = True
                        break

        if not completed:
            raw_json = text[start:]

            objects.append(
                {
                    "status": "incomplete_json",
                    "raw": raw_json,
                    "value": regex_json_fallback(raw_json),
                }
            )

            break

    return objects


def regex_json_fallback(text):
    """
    Recovers useful fields from malformed JSON.
    """

    result = {}

    field_names = [
        "ProcessorId",
        "IssuerId",
        "TransactionId",
        "StepupRequestId",
        "VerificationToken",
        "OtpReferenceCode",
        "Status",
        "StepupType",
        "Language",
        "Description",
        "Message",
        "ReferenceNumber",
        "ResponseCode",
        "BankOrg",
        "CustomerId",
        "CardUuid",
        "CardholderName",
        "CardProvider",
        "CardType",
        "EnableOOB",
        "statusResponseEnum",
        "cardBlocked",
    ]

    for field in field_names:
        match = re.search(
            rf'"{re.escape(field)}"\s*:\s*'
            r'(?:"(?P<string>.*?)"|'
            r"(?P<literal>true|false|null|-?\d+(?:\.\d+)?))",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if match:
            value = (
                match.group("string")
                if match.group("string") is not None
                else match.group("literal")
            )

            if value == "true":
                value = True
            elif value == "false":
                value = False
            elif value == "null":
                value = None

            result[field] = value

    return result


# ============================================================
# JSON normalization
# ============================================================


def nested_get(data, *keys):
    current = data

    for key in keys:
        if not isinstance(current, dict):
            return None

        current = current.get(key)

        if current is None:
            return None

    return current


def find_values_recursive(value, target_keys):
    found = defaultdict(list)
    target_lower = {key.lower() for key in target_keys}

    def walk(item):
        if isinstance(item, dict):
            for key, nested_value in item.items():
                if str(key).lower() in target_lower:
                    found[str(key)].append(nested_value)

                walk(nested_value)

        elif isinstance(item, list):
            for nested_value in item:
                walk(nested_value)

    walk(value)

    return dict(found)


def normalize_credentials(payload):
    credentials = payload.get("Credentials", [])

    result = {"items": [], "mobile": None, "email": None, "oob_credential_id": None}

    if not isinstance(credentials, list):
        return result

    for credential in credentials:
        if not isinstance(credential, dict):
            continue

        item = {
            "id": credential.get("Id"),
            "type": credential.get("Type"),
            "text": credential.get("Text"),
        }

        result["items"].append(item)

        credential_type = str(credential.get("Type", "")).upper()

        text = credential.get("Text")

        if "SMS" in credential_type:
            result["mobile"] = result["mobile"] or text

        elif "EMAIL" in credential_type:
            result["email"] = result["email"] or text

        elif "OUTOFBAND" in credential_type:
            result["oob_credential_id"] = result["oob_credential_id"] or credential.get(
                "Id"
            )

    return result


def normalize_json_payload(payload):
    if not isinstance(payload, dict):
        return {}

    credentials = normalize_credentials(payload)

    merchant_info = payload.get("MerchantInfo") or {}
    transaction_info = payload.get("TransactionInfo") or {}
    payment_info = payload.get("PaymentInfo") or {}
    error = payload.get("Error") or {}

    recursive = find_values_recursive(
        payload,
        {
            "statusResponseEnum",
            "cardBlocked",
            "customerId",
            "cardUuid",
            "cardholderName",
            "cardProvider",
            "cardType",
            "bankOrg",
            "enableOOB",
            "method",
            "status",
        },
    )

    return {
        "processor_id": payload.get("ProcessorId"),
        "issuer_id": payload.get("IssuerId"),
        "transaction_id": payload.get("TransactionId"),
        "stepup_request_id": payload.get("StepupRequestId"),
        "verification_token": payload.get("VerificationToken"),
        "otp_reference_code": payload.get("OtpReferenceCode"),
        "status": payload.get("Status"),
        "stepup_type": payload.get("StepupType"),
        "language": payload.get("Language"),
        "customer": {
            "customer_id": payload.get("CustomerId")
            or first_recursive(recursive, "customerId"),
            "mobile": credentials.get("mobile"),
            "email": credentials.get("email"),
        },
        "credentials": credentials,
        "merchant": {
            "id": merchant_info.get("MerchantId"),
            "name": merchant_info.get("MerchantName"),
            "url": merchant_info.get("MerchantURL"),
            "category_code": merchant_info.get("MerchantCategoryCode"),
            "country_code": merchant_info.get("MerchantCountryCode"),
            "acquirer_id": merchant_info.get("AcquirerId"),
        },
        "transaction": {
            "amount": transaction_info.get("TransactionAmount"),
            "currency": transaction_info.get("TransactionCurrency"),
            "timestamp": transaction_info.get("TransactionTimeStamp"),
            "exponent": transaction_info.get("TransactionExponent"),
        },
        "payment": {
            "card_number": payment_info.get("CardNumber"),
            "expiry_month": payment_info.get("CardExpiryMonth"),
            "expiry_year": payment_info.get("CardExpiryYear"),
            "card_holder_name": payment_info.get("CardHolderName"),
            "card_type": payment_info.get("CardType"),
        },
        "cardinal_error": {
            "reference_number": error.get("ReferenceNumber"),
            "description": error.get("Description"),
            "message": error.get("Message"),
        },
        "oob": {
            "status": first_recursive(recursive, "statusResponseEnum"),
            "card_blocked": first_recursive(recursive, "cardBlocked"),
            "method": first_recursive(recursive, "method"),
            "bank_org": first_recursive(recursive, "bankOrg"),
            "enabled": first_recursive(recursive, "enableOOB"),
            "card_uuid": first_recursive(recursive, "cardUuid"),
            "cardholder_name": first_recursive(recursive, "cardholderName"),
            "card_provider": first_recursive(recursive, "cardProvider"),
            "card_type": first_recursive(recursive, "cardType"),
        },
    }


def first_recursive(values, key):
    for actual_key, items in values.items():
        if actual_key.lower() == key.lower() and items:
            return items[0]

    return None


# ============================================================
# Error and stack-trace parsing
# ============================================================


def parse_stack_trace(lines):
    frames = []
    non_frames = []

    for line in lines:
        match = STACK_FRAME_RE.match(line)

        if match:
            frames.append(
                {
                    "method": clean_text(match.group("method")),
                    "file": clean_text(match.group("file")) or None,
                    "line": (int(match.group("line")) if match.group("line") else None),
                }
            )
        else:
            non_frames.append(line)

    return frames, non_frames


def parse_exception(message, continuation_lines):
    combined = "\n".join([message] + continuation_lines)

    exception_type = None
    exception_message = None

    match = re.search(
        r"(?P<type>"
        r"(?:System\.)?"
        r"[A-Za-z][A-Za-z0-9_.]*(?:Exception|Error)"
        r")\s*:\s*"
        r"(?P<message>[^\n]+)",
        combined,
    )

    if match:
        exception_type = match.group("type")
        exception_message = clean_text(match.group("message"))

    frames, non_frames = parse_stack_trace(continuation_lines)

    return {
        "type": exception_type,
        "message": exception_message,
        "stack_trace": "\n".join(continuation_lines) or None,
        "frames": frames,
        "additional_lines": non_frames,
    }


# ============================================================
# Event classification
# ============================================================


def classify_event(message):
    lower = message.lower()

    if "error on stepupcall v+" in lower:
        return "vplus_mq_timeout"

    if "oob tracker id:" in lower and "error http" in lower:
        return "oob_http_error"

    if "webexception:" in lower:
        return "web_exception"

    if "exception during validate api call" in lower:
        return "oob_validate_exception"

    if "oob status api returned null or empty" in lower:
        return "oob_empty_status_response"

    if "invokeoobapiget" in lower and "error code:" in lower:
        return "oob_status_api_error"

    if "request body" in lower:
        return "request_body"

    if "stepup response to cardinal" in lower or "stepup responce to cardinal" in lower:
        return "cardinal_stepup_response"

    if (
        "validatecall response to cardinal" in lower
        or "validate response to cardinal" in lower
    ):
        return "cardinal_validate_response"

    if "stepupcall v+ input message" in lower:
        return "vplus_input"

    if "stepupcall v+ response message" in lower:
        return "vplus_response"

    if "initiateactioncallcontroller" in lower:
        if "placed in queue" in lower:
            return "otp_queue"

        if "input message" in lower:
            return "otp_input"

        return "initiate_action"

    if "otp processed successfully" in lower:
        return "otp_success"

    if "validatecall" in lower:
        return "validate_call"

    if "/process/authenticate" in lower:
        return "oob_authenticate_api"

    if "/process/validate" in lower:
        return "oob_validate_api"

    if "/process/status/" in lower or "/status/" in lower:
        return "oob_status_poll"

    if "responsecode=" in lower or "responsecode:" in lower:
        return "vplus_result"

    if "error" in lower or "exception" in lower:
        return "error"

    return "message"


def infer_phase(tracker_no):
    if not tracker_no:
        return "UNKNOWN"

    prefix = tracker_no[:2].upper()

    return {"SU": "STEP_UP", "IA": "INITIATE_ACTION", "VL": "VALIDATE"}.get(
        prefix, prefix
    )


def infer_severity(event_type, message):
    lower = message.lower()

    if event_type in {
        "vplus_mq_timeout",
        "oob_http_error",
        "web_exception",
        "oob_validate_exception",
        "oob_empty_status_response",
        "oob_status_api_error",
        "error",
    }:
        return "ERROR"

    if (
        'status":"error"' in lower
        or '"status": "error"' in lower
        or "mq timeout" in lower
        or "host unreachable" in lower
    ):
        return "ERROR"

    if "pending" in lower:
        return "INFO"

    return "INFO"


# ============================================================
# Per-event parsing
# ============================================================


def parse_event(raw_event, source_file, event_no):
    timestamp_raw = raw_event.get("timestamp_raw")
    tracker_no = raw_event.get("tracker_no")

    message_lines = raw_event.get("message_lines", [])
    message = clean_text("\n".join(message_lines))

    first_message = message_lines[0] if message_lines else ""

    continuation_lines = message_lines[1:] if len(message_lines) > 1 else []

    event_type = classify_event(message)
    severity = infer_severity(event_type, message)

    json_objects = extract_json_objects(message)

    normalized_payloads = []

    for item in json_objects:
        if isinstance(item.get("value"), dict):
            normalized_payloads.append(normalize_json_payload(item["value"]))

    stack = parse_exception(first_message, continuation_lines)

    urls = unique(match.group("url") for match in URL_RE.finditer(message))

    uuid_values = unique(UUID_RE.findall(message))

    stepup_ids = []

    for normalized in normalized_payloads:
        stepup_id = normalized.get("stepup_request_id")

        if stepup_id:
            stepup_ids.append(stepup_id)

    for url in urls:
        match = STEPUP_ID_PATH_RE.search(url)

        if match:
            stepup_ids.append(match.group("stepup_id"))

    stepup_ids = unique(stepup_ids)

    response_code = match_value(RESPONSE_CODE_RE, message, "response_code")

    bank_org = match_value(BANK_ORG_RE, message, "bank_org")

    customer_id = match_value(CUSTOMER_ID_RE, message, "customer_id")

    enable_oob = match_value(ENABLE_OOB_RE, message, "enable_oob")

    status_enum = match_value(STATUS_ENUM_RE, message, "status")

    card_blocked_raw = match_value(CARD_BLOCKED_RE, message, "card_blocked")

    event = {
        "event_no": event_no,
        "source_file": source_file,
        "physical_line_start": raw_event.get("physical_line_start"),
        "physical_line_end": raw_event.get("physical_line_end"),
        "timestamp": (parse_timestamp(timestamp_raw) if timestamp_raw else None),
        "timestamp_raw": timestamp_raw,
        "tracker_no": tracker_no,
        "tracker_type": (tracker_no[:2] if tracker_no else None),
        "phase": infer_phase(tracker_no),
        "event_type": event_type,
        "severity": severity,
        "message": first_message,
        "raw": message,
        "identifiers": {
            "oob_tracker_id": match_value(OOB_TRACKER_RE, message, "oob_tracker_id"),
            "stepup_request_ids": stepup_ids,
            "uuids": uuid_values,
        },
        "http": {
            "status_code": match_value(HTTP_STATUS_RE, message, "http_status"),
            "error_code": match_value(ERROR_CODE_RE, message, "error_code"),
            "urls": urls,
        },
        "vplus": {
            "response_code": response_code,
            "bank_org": bank_org,
            "customer_id": customer_id,
            "enable_oob": enable_oob,
        },
        "oob": {"status": status_enum, "card_blocked": to_bool(card_blocked_raw)},
        "queue": {
            "names": unique(QUEUE_RE.findall(message)),
            "message_id": match_value(MSG_ID_RE, message, "msg_id"),
        },
        "observables": {
            "mobile_numbers": unique(MOBILE_RE.findall(message)),
            "email_addresses": unique(EMAIL_RE.findall(message)),
            "ip_addresses": unique(IP_RE.findall(message)),
        },
        "json_payloads": json_objects,
        "normalized_payloads": normalized_payloads,
        "exception": stack,
        "parse_status": "parsed",
        "warnings": [],
    }

    if not timestamp_raw:
        event["parse_status"] = "partial"
        event["warnings"].append("Timestamp was not found")

    if not tracker_no:
        event["parse_status"] = "partial"
        event["warnings"].append("Tracker number was not found")

    if "{" in message and not json_objects:
        event["parse_status"] = "partial"
        event["warnings"].append("JSON marker found but no JSON object was recovered")

    if any(item.get("status") != "parsed" for item in json_objects):
        event["parse_status"] = "partial"
        event["warnings"].append("One or more JSON objects required fallback parsing")

    return event


# ============================================================
# Correlation helpers
# ============================================================


def put_if_empty(target, key, value):
    if target.get(key) in (None, "", [], {}):
        if value not in (None, "", [], {}):
            target[key] = value


def new_flow(flow_id):
    return {
        "flow_id": flow_id,
        "transaction_id": None,
        "stepup_request_ids": [],
        "trackers": [],
        "first_timestamp": None,
        "last_timestamp": None,
        "processor_id": None,
        "issuer_id": None,
        "bank_org": None,
        "customer_id": None,
        "authentication": {
            "type": None,
            "status": None,
            "verification_token": None,
            "otp_reference_code": None,
            "otp_processed": False,
            "sms_queued": False,
        },
        "customer": {"mobile": None, "email": None},
        "merchant": {
            "id": None,
            "name": None,
            "url": None,
            "category_code": None,
            "country_code": None,
        },
        "transaction": {"amount": None, "currency": None, "timestamp": None},
        "payment": {
            "card_number": None,
            "expiry_month": None,
            "expiry_year": None,
            "card_holder_name": None,
            "card_type": None,
        },
        "oob": {
            "enabled": None,
            "status_history": [],
            "card_blocked_history": [],
            "http_calls": [],
            "oob_tracker_ids": [],
        },
        "errors": [],
        "event_references": [],
        "warnings": [],
        "integrity_status": "OK",
    }


def update_time_range(flow, timestamp):
    if not timestamp:
        return

    if flow["first_timestamp"] is None or timestamp < flow["first_timestamp"]:
        flow["first_timestamp"] = timestamp

    if flow["last_timestamp"] is None or timestamp > flow["last_timestamp"]:
        flow["last_timestamp"] = timestamp


def merge_normalized(flow, normalized):
    put_if_empty(flow, "transaction_id", normalized.get("transaction_id"))

    put_if_empty(flow, "processor_id", normalized.get("processor_id"))

    put_if_empty(flow, "issuer_id", normalized.get("issuer_id"))

    stepup_id = normalized.get("stepup_request_id")

    if stepup_id and stepup_id not in flow["stepup_request_ids"]:
        flow["stepup_request_ids"].append(stepup_id)

    put_if_empty(flow["authentication"], "type", normalized.get("stepup_type"))

    put_if_empty(
        flow["authentication"],
        "verification_token",
        normalized.get("verification_token"),
    )

    put_if_empty(
        flow["authentication"],
        "otp_reference_code",
        normalized.get("otp_reference_code"),
    )

    status = normalized.get("status")

    if status:
        flow["authentication"]["status"] = status

    customer = normalized.get("customer", {})

    put_if_empty(flow["customer"], "customer_id", customer.get("customer_id"))

    put_if_empty(flow["customer"], "mobile", customer.get("mobile"))

    put_if_empty(flow["customer"], "email", customer.get("email"))

    merchant = normalized.get("merchant", {})

    for key in flow["merchant"]:
        put_if_empty(flow["merchant"], key, merchant.get(key))

    transaction = normalized.get("transaction", {})

    for key in flow["transaction"]:
        put_if_empty(flow["transaction"], key, transaction.get(key))

    payment = normalized.get("payment", {})

    for key in flow["payment"]:
        put_if_empty(flow["payment"], key, payment.get(key))

    oob = normalized.get("oob", {})

    put_if_empty(flow, "bank_org", oob.get("bank_org"))

    put_if_empty(flow, "customer_id", customer.get("customer_id"))

    put_if_empty(flow["oob"], "enabled", oob.get("enabled"))

    if oob.get("status"):
        flow["oob"]["status_history"].append(oob["status"])

    if oob.get("card_blocked") is not None:
        flow["oob"]["card_blocked_history"].append(oob["card_blocked"])

    cardinal_error = normalized.get("cardinal_error", {})

    if any(cardinal_error.values()):
        flow["errors"].append({"source": "cardinal_response", **cardinal_error})


def build_flows(events):
    """
    Correlates by TransactionId first, then StepupRequestId,
    then Tracker No where no stronger identifier exists.
    """

    alias_to_flow = {}
    flows = {}

    def resolve_flow(event):
        aliases = []

        for normalized in event.get("normalized_payloads", []):
            txid = normalized.get("transaction_id")

            stepup_id = normalized.get("stepup_request_id")

            if txid:
                aliases.append(f"tx:{txid}")

            if stepup_id:
                aliases.append(f"stepup:{stepup_id}")

        for stepup_id in event.get("identifiers", {}).get("stepup_request_ids", []):
            aliases.append(f"stepup:{stepup_id}")

        tracker = event.get("tracker_no")

        if tracker:
            aliases.append(f"tracker:{tracker}")

        existing_id = None

        for alias in aliases:
            if alias in alias_to_flow:
                existing_id = alias_to_flow[alias]
                break

        if existing_id is None:
            existing_id = aliases[0] if aliases else f"event:{event['event_no']}"

            flows[existing_id] = new_flow(existing_id)

        for alias in aliases:
            alias_to_flow[alias] = existing_id

        return flows[existing_id]

    for event in events:
        flow = resolve_flow(event)

        tracker = event.get("tracker_no")

        if tracker and tracker not in flow["trackers"]:
            flow["trackers"].append(tracker)

        update_time_range(flow, event.get("timestamp"))

        flow["event_references"].append(
            {
                "event_no": event["event_no"],
                "source_file": event["source_file"],
                "timestamp": event["timestamp"],
                "tracker_no": tracker,
                "event_type": event["event_type"],
                "severity": event["severity"],
            }
        )

        for normalized in event.get("normalized_payloads", []):
            merge_normalized(flow, normalized)

        put_if_empty(flow, "bank_org", event.get("vplus", {}).get("bank_org"))

        put_if_empty(flow, "customer_id", event.get("vplus", {}).get("customer_id"))

        put_if_empty(flow["oob"], "enabled", event.get("vplus", {}).get("enable_oob"))

        oob_status = event.get("oob", {}).get("status")

        if oob_status:
            flow["oob"]["status_history"].append(oob_status)

        card_blocked = event.get("oob", {}).get("card_blocked")

        if card_blocked is not None:
            flow["oob"]["card_blocked_history"].append(card_blocked)

        oob_tracker_id = event.get("identifiers", {}).get("oob_tracker_id")

        if oob_tracker_id and oob_tracker_id not in flow["oob"]["oob_tracker_ids"]:
            flow["oob"]["oob_tracker_ids"].append(oob_tracker_id)

        http = event.get("http", {})

        if http.get("status_code") or http.get("error_code") or http.get("urls"):
            flow["oob"]["http_calls"].append(
                {
                    "timestamp": event["timestamp"],
                    "event_type": event["event_type"],
                    "status_code": http.get("status_code"),
                    "error_code": http.get("error_code"),
                    "urls": http.get("urls"),
                }
            )

        if event["event_type"] == "otp_success":
            flow["authentication"]["otp_processed"] = True

        if event["event_type"] == "otp_queue":
            flow["authentication"]["sms_queued"] = True

        if event["severity"] == "ERROR":
            flow["errors"].append(
                {
                    "timestamp": event["timestamp"],
                    "tracker_no": tracker,
                    "event_type": event["event_type"],
                    "message": event["message"],
                    "http_status": http.get("status_code"),
                    "error_code": http.get("error_code"),
                    "exception": event.get("exception"),
                    "json_payloads": event.get("json_payloads"),
                }
            )

    output = list(flows.values())

    for flow in output:
        flow["oob"]["status_history"] = unique(flow["oob"]["status_history"])

        flow["oob"]["card_blocked_history"] = unique(
            flow["oob"]["card_blocked_history"]
        )

        warnings = []

        if not flow["transaction_id"]:
            warnings.append("No TransactionId found")

        if not flow["trackers"]:
            warnings.append("No tracker found")

        if flow["authentication"]["type"] == "OTP":
            if not flow["authentication"]["sms_queued"]:
                warnings.append("OTP flow has no SMS queue confirmation")

            if not flow["authentication"]["otp_processed"]:
                warnings.append("OTP flow has no processed-success confirmation")

        if (
            flow["authentication"]["type"] == "OUTOFBAND"
            or flow["oob"]["status_history"]
        ):
            history = flow["oob"]["status_history"]

            if history and history[-1] == "PENDING":
                warnings.append("OOB flow remains pending in the captured log")

        if flow["errors"]:
            warnings.append("Flow contains one or more errors")

        flow["warnings"] = warnings
        flow["integrity_status"] = "CHECK" if warnings else "OK"

    return output


# ============================================================
# Summary
# ============================================================


def build_summary(events, flows):
    event_types = defaultdict(int)
    severities = defaultdict(int)
    tracker_types = defaultdict(int)
    parse_statuses = defaultdict(int)
    error_categories = defaultdict(int)
    cardinal_statuses = defaultdict(int)
    oob_statuses = defaultdict(int)

    for event in events:
        event_types[event["event_type"]] += 1
        severities[event["severity"]] += 1
        parse_statuses[event["parse_status"]] += 1

        tracker_type = event.get("tracker_type") or "UNKNOWN"

        tracker_types[tracker_type] += 1

        if event["severity"] == "ERROR":
            error_categories[event["event_type"]] += 1

    for flow in flows:
        status = flow.get("authentication", {}).get("status") or "UNKNOWN"

        cardinal_statuses[status] += 1

        for status_value in flow.get("oob", {}).get("status_history", []):
            oob_statuses[status_value] += 1

    return {
        "total_events": len(events),
        "total_flows": len(flows),
        "event_type_counts": dict(sorted(event_types.items())),
        "severity_counts": dict(sorted(severities.items())),
        "tracker_type_counts": dict(sorted(tracker_types.items())),
        "parse_status_counts": dict(sorted(parse_statuses.items())),
        "error_category_counts": dict(sorted(error_categories.items())),
        "cardinal_status_counts": dict(sorted(cardinal_statuses.items())),
        "oob_status_counts": dict(sorted(oob_statuses.items())),
        "flows_requiring_check": sum(
            1 for flow in flows if flow["integrity_status"] == "CHECK"
        ),
    }


# ============================================================
# File parser
# ============================================================


def parse_files(input_files):
    events = []
    event_no = 0

    for input_file in input_files:
        raw_events = load_logical_events(input_file)

        for raw_event in raw_events:
            event_no += 1

            events.append(parse_event(raw_event, Path(input_file).name, event_no))

    flows = build_flows(events)
    summary = build_summary(events, flows)

    errors = [event for event in events if event["severity"] == "ERROR"]

    partial_events = [event for event in events if event["parse_status"] != "parsed"]

    return (events, flows, errors, partial_events, summary)


def save_json(path, value):
    with Path(path).open("w", encoding="utf-8") as file:
        json.dump(value, file, indent=4, ensure_ascii=False)


# ---------------------------------------------------------------------------
# LLens integration -- everything above is unmodified from the provided
# script (only the argparse-based CLI / main()/__main__ block was dropped,
# consistent with the other custom parsers in this directory; the "html"
# import was also unused by the original script and dropped along with it).
#
# parse_files() takes a LIST of file paths (it was designed to merge several
# Cardinal log files into one correlated view) and returns
# (events, flows, errors, partial_events, summary). LLens only ever hands a
# parser one file at a time, so the adapter below calls parse_files() with a
# single-element list. events is the flat per-line stream; flows is the
# cross-event correlation by TransactionId -> StepupRequestId -> tracker
# (same shape/intent as parser_AFS_Netcetera.py's transactions and
# parser_Debit_Transaction.py's flows). errors/partial_events are already
# derivable from each event's own severity/parse_status, so the adapter
# doesn't need them separately -- every event (parsed, partial, or error)
# is still emitted, consistent with every other parser in this directory
# never dropping a line.
# ---------------------------------------------------------------------------

DISPLAY_NAME = "Cardinal OTP/StepUp/OOB Log (Multi-Flow Correlation)"
DEFAULT_SOURCE_SYSTEM = "cardinal_stepup_oob_log"


def detect(sample_text: str) -> bool:
    if not (TIMESTAMP_ANYWHERE_RE.search(sample_text) or EVENT_START_RE.search(sample_text)):
        return False

    lowered = sample_text.lower()
    distinctive_markers = (
        "cardinal",
        "oob tracker id",
        "invokeoobapiget",
        "/process/status/",
        "/process/validate",
        "/process/authenticate",
        "out-of-band",
    )
    return any(marker in lowered for marker in distinctive_markers)


def parse_log_file(log_file_path, output_json_path=None):
    """Adapter for the LLens custom-parser registry. Calls the original,
    unmodified parse_files() above (with a single-element file list) and
    flattens its (events, flows, ...) output into the flat record shape the
    registry expects (see custom_parser_registry.py)."""
    events, flows, _errors, _partial_events, _summary = parse_files([log_file_path])

    flow_by_event_no = {}
    for flow in flows:
        for ref in flow.get("event_references", []):
            flow_by_event_no[ref["event_no"]] = flow

    out_records = []
    for event in events:
        flow = flow_by_event_no.get(event["event_no"])

        correlation_id = None
        if flow:
            correlation_id = (
                flow.get("transaction_id")
                or (flow["stepup_request_ids"][0] if flow.get("stepup_request_ids") else None)
                or (flow["trackers"][0] if flow.get("trackers") else None)
            )
        correlation_id = correlation_id or event.get("tracker_no")

        details = {
            "event_type": event.get("event_type"),
            "phase": event.get("phase"),
            "parse_status": event.get("parse_status"),
            "warnings": event.get("warnings"),
            "identifiers": event.get("identifiers"),
            "http": event.get("http"),
            "vplus": event.get("vplus"),
            "oob": event.get("oob"),
            "queue": event.get("queue"),
            "observables": event.get("observables"),
            "normalized_payloads": event.get("normalized_payloads"),
            "exception": event.get("exception"),
        }
        if flow:
            details["flow"] = {
                "flow_id": flow.get("flow_id"),
                "transaction_id": flow.get("transaction_id"),
                "stepup_request_ids": flow.get("stepup_request_ids"),
                "trackers": flow.get("trackers"),
                "processor_id": flow.get("processor_id"),
                "issuer_id": flow.get("issuer_id"),
                "bank_org": flow.get("bank_org"),
                "customer_id": flow.get("customer_id"),
                "authentication": flow.get("authentication"),
                "customer": flow.get("customer"),
                "merchant": flow.get("merchant"),
                "transaction": resolve_transaction_currency(flow.get("transaction")),
                "payment": flow.get("payment"),
                "oob": flow.get("oob"),
                "integrity_status": flow.get("integrity_status"),
                "warnings": flow.get("warnings"),
            }

        out_records.append(
            {
                "timestamp": event.get("timestamp"),
                "log_level": event.get("severity") or "INFO",
                "correlation_id": correlation_id,
                "log_type": event.get("event_type"),
                "action": event.get("message") or "(no message)",
                "details": details,
                "_raw_block": (
                    f'{event["timestamp_raw"]} {event.get("raw")}'
                    if event.get("timestamp_raw")
                    else event.get("raw")
                ),
            }
        )

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(out_records, f, indent=2, default=str)

    return out_records
