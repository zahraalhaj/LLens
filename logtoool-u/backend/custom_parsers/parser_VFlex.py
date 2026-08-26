import re
import json
import html
import base64
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from backend.core.currency import resolve_transaction_currency

# ============================================================
# Log patterns
# ============================================================

TIMESTAMP_TEXT = r"\d{1,2}/\d{1,2}/\d{4}\s+" r"\d{1,2}:\d{2}:\d{2}\s+" r"(?:AM|PM)"

TIMESTAMP_RE = re.compile(TIMESTAMP_TEXT)

TRACKED_EVENT_RE = re.compile(
    rf"^(?P<timestamp>{TIMESTAMP_TEXT})\s+"
    r"Log Tracker No:\s*"
    r"(?P<tracker>[A-Z]{2}\d+)\s*"
    r"(?:=>|=\\>|=&gt;)\s*"
    r"(?P<message>.*)$",
    re.IGNORECASE | re.DOTALL,
)

UNTRACKED_EVENT_RE = re.compile(
    rf"^(?P<timestamp>{TIMESTAMP_TEXT})\s*"
    r"(?:-|=)?(?:>|\\>)?\s*"
    r"(?P<message>.*)$",
    re.IGNORECASE | re.DOTALL,
)

TRACKER_RE = re.compile(r"\b(?P<tracker>(?:SU|IA|VL)\d{18})\b", re.IGNORECASE)

VF_INPUT_RE = re.compile(
    r"TrackingID\s*(?P<tracker>(?:SU|IA|VL)\d+)\s*,?\s*"
    r"CardNo\s*(?P<card>[A-Za-z0-9Xx*]+)\s*,?\s*"
    r"ExpDate\s*(?P<expiry>\d{4})",
    re.IGNORECASE,
)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

MSG_ID_RE = re.compile(r"MsgId:\s*(?P<msg_id>[A-Za-z0-9\-]+)", re.IGNORECASE)

QUEUE_RE = re.compile(r"\((?P<queue>AFS\.[A-Za-z0-9._\-]+)\)", re.IGNORECASE)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@" r"[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

MOBILE_RE = re.compile(r"(?<!\d)\+?\d{8,15}(?!\d)")

MASKED_CARD_RE = re.compile(r"\b(?:X{4,}|\*{4,})\d{4}\b", re.IGNORECASE)

OTP_MESSAGE_RE = re.compile(
    r"(?:Dear\s+Customer,\s*)?"
    r"(?P<otp>\d{4,8})\s+is\s+your\s+OTP"
    r".*?\bcard\s+(?P<card>[Xx*]+\d{4})"
    r"\s+at\s+(?P<merchant>.*?)"
    r"\s+for\s+(?P<currency>[A-Z]{3})"
    r"\s+(?P<amount>\d+(?:\.\d+)?)\.",
    re.IGNORECASE | re.DOTALL,
)

ERROR_WORD_RE = re.compile(
    r"\b(error|failed|failure|exception|timeout|unreachable)\b", re.IGNORECASE
)


# ============================================================
# Basic helpers
# ============================================================


def clean_text(value):
    if value is None:
        return ""

    return str(value).replace("\x00", "").replace("﻿", "").strip()


def normalize_log_encoding(value):
    """
    Normalizes escaping observed in exported logs:

        =\\>       -> =>
        \\<Msg\\>  -> <Msg>
        \\[        -> [
        \\]        -> ]
        &lt;       -> <
        &gt;       -> >
    """

    value = clean_text(value)

    value = value.replace("=\\>", "=>")
    value = value.replace("\\<", "<")
    value = value.replace("\\>", ">")
    value = value.replace("\\[", "[")
    value = value.replace("\\]", "]")

    return clean_text(value)


def parse_timestamp(value):
    try:
        return datetime.strptime(value, "%m/%d/%Y %I:%M:%S %p").isoformat(sep=" ")
    except Exception:
        return value


def to_float(value):
    value = clean_text(value)

    if not value:
        return None

    try:
        return float(value)
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


def put_if_empty(target, key, value):
    if value in (None, "", [], {}):
        return

    if target.get(key) in (None, "", [], {}):
        target[key] = value


def nested_get(data, *keys):
    current = data

    for key in keys:
        if not isinstance(current, dict):
            return None

        current = current.get(key)

        if current is None:
            return None

    return current


# ============================================================
# Physical lines to logical events
# ============================================================


def iter_logical_events(input_file):
    """
    Streams the input file.

    A timestamp starts a new logical event. Any continuation line
    without a timestamp is attached to the preceding event.
    """

    current = None
    orphan_number = 0

    with Path(input_file).open("r", encoding="utf-8", errors="replace") as file:

        for line_number, physical_line in enumerate(file, start=1):
            physical_line = clean_text(physical_line)

            if not physical_line:
                continue

            timestamp_match = TIMESTAMP_RE.match(physical_line)

            if timestamp_match:
                if current:
                    yield current

                current = {
                    "physical_line_start": line_number,
                    "physical_line_end": line_number,
                    "lines": [physical_line],
                }

            elif current:
                current["physical_line_end"] = line_number
                current["lines"].append(physical_line)

            else:
                orphan_number += 1

                yield {
                    "physical_line_start": line_number,
                    "physical_line_end": line_number,
                    "lines": [physical_line],
                    "orphan_number": orphan_number,
                }

        if current:
            yield current


def split_compacted_file(input_file):
    """
    Fallback for files exported as one very long physical line.
    """

    content = Path(input_file).read_text(encoding="utf-8", errors="replace")

    content = clean_text(content)
    matches = list(TIMESTAMP_RE.finditer(content))

    events = []

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)

        events.append(
            {
                "physical_line_start": None,
                "physical_line_end": None,
                "lines": [clean_text(content[start:end])],
            }
        )

    return events


def load_logical_events(input_file):
    events = list(iter_logical_events(input_file))

    timestamped = sum(
        1
        for event in events
        if event.get("lines") and TIMESTAMP_RE.match(event["lines"][0])
    )

    if timestamped == 0:
        content = Path(input_file).read_text(encoding="utf-8", errors="replace")

        if TIMESTAMP_RE.search(content):
            return split_compacted_file(input_file)

    return events


# ============================================================
# Balanced JSON extraction
# ============================================================


def extract_json_objects(text):
    """
    Extracts every balanced JSON object from an event.
    Handles nested objects, arrays and quoted braces.
    """

    text = normalize_log_encoding(text)
    objects = []
    cursor = 0

    while cursor < len(text):
        start = text.find("{", cursor)

        if start == -1:
            break

        depth = 0
        in_string = False
        escaped = False
        completed = False

        for position in range(start, len(text)):
            character = text[position]

            if escaped:
                escaped = False
                continue

            if character == "\\":
                escaped = True
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
                            value = json.loads(raw_json)

                            objects.append(
                                {
                                    "parse_status": "parsed",
                                    "raw": raw_json,
                                    "value": value,
                                }
                            )

                        except Exception as exception:
                            objects.append(
                                {
                                    "parse_status": "fallback",
                                    "raw": raw_json,
                                    "error": str(exception),
                                    "value": fallback_json_fields(raw_json),
                                }
                            )

                        cursor = position + 1
                        completed = True
                        break

        if not completed:
            raw_json = text[start:]

            objects.append(
                {
                    "parse_status": "incomplete",
                    "raw": raw_json,
                    "value": fallback_json_fields(raw_json),
                }
            )

            break

    return objects


def fallback_json_fields(raw_json):
    """
    Recovers important values if JSON decoding fails.
    """

    fields = [
        "ProcessorId",
        "IssuerId",
        "TransactionId",
        "StepupRequestId",
        "VerificationToken",
        "Status",
        "StepupType",
        "Language",
        "ReferenceNumber",
        "Description",
        "Message",
        "corelationId",
        "tranDate",
        "tranTime",
        "tranRef",
        "cardHash",
        "cardExpiry",
        "clientCustomerId",
        "emailId",
        "mobileNumber",
        "orgNo",
        "otp",
        "merchantName",
        "last4digitPAN",
        "bankName",
        "transactionCurrency",
        "transactionAmount",
        "channel",
        "lang",
        "templateID",
        "success",
    ]

    result = {}

    for field in fields:
        match = re.search(
            rf'"{re.escape(field)}"\s*:\s*'
            r'(?:"(?P<string>.*?)"|'
            r"(?P<literal>true|false|null|-?\d+(?:\.\d+)?))",
            raw_json,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if not match:
            continue

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
        elif re.fullmatch(r"-?\d+\.\d+", str(value)):
            value = float(value)

        result[field] = value

    return result


# ============================================================
# Base64 decoding
# ============================================================


def decode_base64_text(value):
    """
    Decodes the Base64 SMS body returned by the bank API.
    Returns None if the value is not valid Base64 text.
    """

    if not isinstance(value, str) or not value:
        return None

    try:
        padded = value + ("=" * (-len(value) % 4))

        decoded = base64.b64decode(padded, validate=True).decode("utf-8")

        printable = sum(
            character.isprintable() or character in "\r\n\t" for character in decoded
        )

        if len(decoded) == 0:
            return None

        if printable / len(decoded) < 0.90:
            return None

        return clean_text(decoded)

    except Exception:
        return None


def parse_decoded_sms(message):
    if not message:
        return {}

    match = OTP_MESSAGE_RE.search(message)

    if not match:
        return {"message": message}

    return {
        "message": message,
        "otp": match.group("otp"),
        "masked_card": clean_text(match.group("card")),
        "merchant": clean_text(match.group("merchant")),
        "currency_text": match.group("currency").upper(),
        "amount": to_float(match.group("amount")),
    }


# ============================================================
# Credential and JSON normalizers
# ============================================================


def normalize_credentials(payload):
    credentials = payload.get("Credentials") or []

    result = {"items": [], "mobile": None, "email": None}

    if not isinstance(credentials, list):
        return result

    for credential in credentials:
        if not isinstance(credential, dict):
            continue

        credential_type = str(credential.get("Type", "")).upper()

        text = credential.get("Text")

        result["items"].append(
            {"id": credential.get("Id"), "type": credential.get("Type"), "text": text}
        )

        if "SMS" in credential_type:
            result["mobile"] = result["mobile"] or text

        elif "EMAIL" in credential_type:
            result["email"] = result["email"] or text

    return result


def detect_json_role(message):
    lower = message.lower()

    if "request body" in lower:
        return "application_request"

    if "get data from bank payload" in lower:
        return "bank_request"

    if "success response from bank api call" in lower:
        return "bank_response"

    if "stepup response to netcetra" in lower or "stepup responce to netcetra" in lower:
        return "netcetera_response"

    return "embedded_json"


def normalize_application_payload(payload):
    credentials = normalize_credentials(payload)

    merchant_info = payload.get("MerchantInfo") or {}
    transaction_info = payload.get("TransactionInfo") or {}
    payment_info = payload.get("PaymentInfo") or {}
    error_info = payload.get("Error") or {}

    return {
        "processor_id": payload.get("ProcessorId"),
        "issuer_id": payload.get("IssuerId"),
        "transaction_id": payload.get("TransactionId"),
        "stepup_request_id": payload.get("StepupRequestId"),
        "verification_token": payload.get("VerificationToken"),
        "message_version": payload.get("MessageVersion"),
        "status": payload.get("Status"),
        "stepup_type": payload.get("StepupType"),
        "language": payload.get("Language"),
        "credentials": credentials,
        "customer": {
            "mobile": credentials.get("mobile"),
            "email": credentials.get("email"),
        },
        "merchant": {
            "acquirer_id": merchant_info.get("AcquirerId"),
            "id": merchant_info.get("MerchantId"),
            "name": merchant_info.get("MerchantName"),
            "url": merchant_info.get("MerchantURL"),
            "category_code": merchant_info.get("MerchantCategoryCode"),
            "country_code": merchant_info.get("MerchantCountryCode"),
        },
        "transaction": {
            "timestamp": transaction_info.get("TransactionTimeStamp"),
            "amount": transaction_info.get("TransactionAmount"),
            "currency": transaction_info.get("TransactionCurrency"),
            "exponent": transaction_info.get("TransactionExponent"),
        },
        "payment": {
            "card_hash": payment_info.get("CardNumber"),
            "expiry_month": payment_info.get("CardExpiryMonth"),
            "expiry_year": payment_info.get("CardExpiryYear"),
            "card_type": payment_info.get("CardType"),
            "card_holder_name": payment_info.get("CardHolderName"),
        },
        "error": {
            "reference_number": error_info.get("ReferenceNumber"),
            "description": error_info.get("Description"),
            "message": error_info.get("Message"),
        },
    }


def normalize_bank_request(payload):
    return {
        "correlation_id": (payload.get("corelationId") or payload.get("correlationId")),
        "bank_transaction": {
            "date": payload.get("tranDate"),
            "time": payload.get("tranTime"),
            "reference": payload.get("tranRef"),
            "card_hash": payload.get("cardHash"),
            "card_expiry": payload.get("cardExpiry"),
            "otp": payload.get("otp"),
            "merchant_name": payload.get("merchantName"),
            "last4_pan": payload.get("last4digitPAN"),
            "bank_name": payload.get("bankName"),
            "currency": payload.get("transactionCurrency"),
            "amount": payload.get("transactionAmount"),
            "channel": payload.get("channel"),
            "language": payload.get("lang"),
            "mobile": payload.get("mobileNumber"),
            "email": payload.get("emailId"),
        },
    }


def normalize_bank_response(payload):
    content = payload.get("content") or {}

    encoded_message = content.get("message")
    decoded_message = decode_base64_text(encoded_message)

    return {
        "success": payload.get("success"),
        "correlation_id": (payload.get("corelationId") or payload.get("correlationId")),
        "content": {
            "card_hash": content.get("cardHash"),
            "client_customer_id": content.get("clientCustomerId"),
            "email": content.get("emailId"),
            "mobile": content.get("mobileNumber"),
            "org_number": content.get("orgNo"),
            "transaction_date": content.get("tranDate"),
            "transaction_time": content.get("tranTime"),
            "transaction_reference": content.get("tranRef"),
            "channel": content.get("channel"),
            "language": content.get("lang"),
            "template_id": content.get("templateID"),
            "message_base64": encoded_message,
            "message_decoded": decoded_message,
            "message_fields": parse_decoded_sms(decoded_message),
        },
    }


def normalize_payload(payload, role):
    if not isinstance(payload, dict):
        return {}

    if role in ("application_request", "netcetera_response"):
        return normalize_application_payload(payload)

    if role == "bank_request":
        return normalize_bank_request(payload)

    if role == "bank_response":
        return normalize_bank_response(payload)

    return payload


# ============================================================
# Escaped XML parsing
# ============================================================


def extract_xml(message):
    normalized = normalize_log_encoding(message)

    # Decode &lt; only after fixing slash escapes.
    normalized = html.unescape(normalized)

    possible_starts = []

    for start_tag in ("<Msg>", "<EmailMsg>"):
        position = normalized.find(start_tag)

        if position != -1:
            possible_starts.append(position)

    if not possible_starts:
        return None

    start = min(possible_starts)

    if normalized[start:].startswith("<Msg>"):
        end_tag = "</Msg>"
    else:
        end_tag = "</EmailMsg>"

    end = normalized.find(end_tag, start)

    if end == -1:
        return clean_text(normalized[start:])

    return clean_text(normalized[start : end + len(end_tag)])


def repair_xml(xml_text):
    if not xml_text:
        return xml_text

    # Protect valid XML entities while fixing naked ampersands.
    xml_text = re.sub(
        r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)", "&amp;", xml_text
    )

    # Repair unusual split closing tags.
    xml_text = re.sub(r"<\s*/\s*([A-Za-z0-9_]+)\s*>", r"</\1>", xml_text)

    return xml_text


def extract_tag(xml_text, tag):
    match = re.search(
        rf"<{re.escape(tag)}>(.*?)</{re.escape(tag)}>",
        xml_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if not match:
        return ""

    return clean_text(html.unescape(match.group(1)))


def xml_find(root, path):
    return clean_text(root.findtext(path))


def parse_sms_xml_regex(xml_text):
    message_text = extract_tag(xml_text, "MsgTxt")

    return {
        "tracking_id": extract_tag(xml_text, "mtrackingid"),
        "org": extract_tag(xml_text, "Org"),
        "type": extract_tag(xml_text, "Typ"),
        "language": extract_tag(xml_text, "Lang"),
        "verify": extract_tag(xml_text, "Verify"),
        "mobile": extract_tag(xml_text, "Mobile"),
        "account_number": extract_tag(xml_text, "AccNo"),
        "message_text": message_text,
        "message_fields": parse_decoded_sms(message_text),
        "parse_method": "regex_fallback",
    }


def parse_sms_xml(xml_text):
    if not xml_text:
        return None, "No SMS XML found"

    try:
        root = ET.fromstring(repair_xml(xml_text))

        if root.tag != "Msg":
            raise ValueError(f"Expected Msg root but found {root.tag}")

        message_text = xml_find(root, "./Body/MsgTxt")

        parsed = {
            "tracking_id": xml_find(root, "./Header/mtrackingid"),
            "org": xml_find(root, "./Header/Org"),
            "type": xml_find(root, "./Header/Typ"),
            "language": xml_find(root, "./Header/Lang"),
            "verify": xml_find(root, "./Header/Verify"),
            "mobile": xml_find(root, "./Header/Mobile"),
            "account_number": xml_find(root, "./Header/AccNo"),
            "message_text": message_text,
            "message_fields": parse_decoded_sms(message_text),
            "parse_method": "xml",
        }

        if not parsed["tracking_id"]:
            raise ValueError("SMS XML is missing mtrackingid")

        return parsed, None

    except Exception as exception:
        parsed = parse_sms_xml_regex(xml_text)

        if parsed.get("tracking_id"):
            parsed["xml_parse_warning"] = str(exception)
            return parsed, None

        return None, str(exception)


# ============================================================
# Event classification
# ============================================================


def classify_event(message):
    lower = message.lower()

    if "sql connection established success" in lower:
        return "sql_connection_success"

    if "request body" in lower:
        return "request_body"

    if "stepupcall vf input message" in lower:
        return "vf_input"

    if "get data from bank payload" in lower:
        return "bank_request"

    if "bank api url" in lower:
        return "bank_api_url"

    if "success response from bank api call" in lower:
        return "bank_api_success_response"

    if "error response from bank api call" in lower:
        return "bank_api_error_response"

    if "stepup response to netcetra" in lower or "stepup responce to netcetra" in lower:
        return "netcetera_stepup_response"

    if "initiateactioncallcontroller" in lower and "sms input message" in lower:
        return "sms_input"

    if "initiateactioncallcontroller" in lower and "sms placed in queue" in lower:
        return "sms_queue"

    if "otp processed successfully" in lower:
        return "otp_success"

    if ERROR_WORD_RE.search(message):
        return "error"

    return "message"


def determine_severity(event_type, message):
    if event_type in ("error", "bank_api_error_response"):
        return "ERROR"

    if ERROR_WORD_RE.search(message):
        return "ERROR"

    return "INFO"


def tracker_phase(tracker_no):
    if not tracker_no:
        return "SYSTEM"

    prefix = tracker_no[:2].upper()

    return {"SU": "STEP_UP", "IA": "INITIATE_ACTION", "VL": "VALIDATE"}.get(
        prefix, prefix
    )


# ============================================================
# Per-event parsing
# ============================================================


def parse_logical_event(raw_event, source_file, event_number):
    physical_text = "\n".join(raw_event.get("lines", []))

    physical_text = normalize_log_encoding(physical_text)

    tracked_match = TRACKED_EVENT_RE.match(physical_text)

    untracked_match = None

    if not tracked_match:
        untracked_match = UNTRACKED_EVENT_RE.match(physical_text)

    if tracked_match:
        timestamp_raw = tracked_match.group("timestamp")
        tracker_no = tracked_match.group("tracker")
        message = clean_text(tracked_match.group("message"))

    elif untracked_match:
        timestamp_raw = untracked_match.group("timestamp")
        message = clean_text(untracked_match.group("message"))

        tracker_match = TRACKER_RE.search(message)

        tracker_no = tracker_match.group("tracker") if tracker_match else None

    else:
        timestamp_raw = None
        tracker_no = None
        message = physical_text

    event_type = classify_event(message)
    severity = determine_severity(event_type, message)

    json_role = detect_json_role(message)
    json_objects = extract_json_objects(message)

    normalized_payloads = []

    for json_object in json_objects:
        value = json_object.get("value")

        normalized_payloads.append(
            {"role": json_role, "data": normalize_payload(value, json_role)}
        )

    event = {
        "event_no": event_number,
        "source_file": source_file,
        "physical_line_start": raw_event.get("physical_line_start"),
        "physical_line_end": raw_event.get("physical_line_end"),
        "timestamp": (parse_timestamp(timestamp_raw) if timestamp_raw else None),
        "timestamp_raw": timestamp_raw,
        "tracker_no": tracker_no,
        "tracker_type": (tracker_no[:2].upper() if tracker_no else None),
        "phase": tracker_phase(tracker_no),
        "event_type": event_type,
        "severity": severity,
        "message": message,
        "raw": physical_text,
        "json_payloads": json_objects,
        "normalized_payloads": normalized_payloads,
        "vf_input": None,
        "bank_api": None,
        "sms": None,
        "queue": None,
        "observables": {
            "urls": unique(URL_RE.findall(message)),
            "emails": unique(EMAIL_RE.findall(message)),
            "mobile_numbers": unique(MOBILE_RE.findall(message)),
            "masked_cards": unique(MASKED_CARD_RE.findall(message)),
        },
        "parse_status": "parsed",
        "warnings": [],
    }

    if event_type == "vf_input":
        vf_match = VF_INPUT_RE.search(message)

        if vf_match:
            event["vf_input"] = {
                "tracking_id": vf_match.group("tracker"),
                "masked_card": vf_match.group("card"),
                "card_expiry": vf_match.group("expiry"),
            }
        else:
            event["parse_status"] = "partial"
            event["warnings"].append("VF input fields could not be fully extracted")

    elif event_type == "bank_api_url":
        urls = URL_RE.findall(message)

        event["bank_api"] = {
            "url": urls[0] if urls else None,
            "operation": infer_bank_operation(urls[0] if urls else None),
        }

    elif event_type == "sms_input":
        xml_text = extract_xml(message)
        parsed_sms, error = parse_sms_xml(xml_text)

        event["sms"] = parsed_sms

        if error:
            event["parse_status"] = "partial"
            event["warnings"].append(error)
            event["sms"] = {"xml_raw": xml_text}

        elif parsed_sms and not event["tracker_no"]:
            event["tracker_no"] = parsed_sms.get("tracking_id")

    elif event_type == "sms_queue":
        queue_match = QUEUE_RE.search(message)
        message_id_match = MSG_ID_RE.search(message)

        event["queue"] = {
            "name": (queue_match.group("queue") if queue_match else None),
            "message_id": (
                message_id_match.group("msg_id") if message_id_match else None
            ),
        }

    if "{" in message and not json_objects:
        event["parse_status"] = "partial"
        event["warnings"].append("JSON marker exists but no JSON object was recovered")

    if any(item.get("parse_status") != "parsed" for item in json_objects):
        event["parse_status"] = "partial"
        event["warnings"].append("One or more JSON payloads required fallback parsing")

    if not timestamp_raw:
        event["parse_status"] = "partial"
        event["warnings"].append("Timestamp was not found")

    # SQL connection event legitimately has no tracker.
    if not tracker_no and event_type != "sql_connection_success":
        event["warnings"].append("Tracker number was not found")

    return event


def infer_bank_operation(url):
    if not url:
        return None

    lower = url.lower()

    if "setup-request" in lower:
        return "SETUP_REQUEST"

    if "otp-request" in lower:
        return "OTP_REQUEST"

    return "UNKNOWN"


# ============================================================
# Transaction correlation
# ============================================================


def new_transaction(key):
    return {
        "record_key": key,
        "transaction_id": None,
        "tracker_no": None,
        "tracker_type": None,
        "phase": None,
        "first_timestamp": None,
        "last_timestamp": None,
        "processor_id": None,
        "issuer_id": None,
        "stepup_request_id": None,
        "verification_token": None,
        "status": None,
        "stepup_type": None,
        "customer": {"client_customer_id": None, "mobile": None, "email": None},
        "payment": {
            "request_card_hash": None,
            "bank_card_hash": None,
            "masked_card": None,
            "last4_pan": None,
            "expiry_month": None,
            "expiry_year": None,
            "card_expiry": None,
        },
        "merchant": {"name": None},
        "transaction": {
            "timestamp": None,
            "date": None,
            "time": None,
            "amount": None,
            "currency": None,
            "currency_text": None,
        },
        "bank_api": {
            "url": None,
            "operation": None,
            "correlation_id": None,
            "transaction_reference": None,
            "success": None,
            "org_number": None,
        },
        "otp": {
            "value": None,
            "channel": None,
            "language": None,
            "template_id": None,
            "sms_message_base64": None,
            "sms_message_decoded": None,
            "sms_xml_message": None,
            "processed_successfully": False,
        },
        "queue": {"name": None, "message_id": None, "queued": False},
        "error": {"reference_number": None, "description": None, "message": None},
        "events": [],
        "warnings": [],
        "integrity_status": "OK",
    }


def update_time_range(record, timestamp):
    if not timestamp:
        return

    if record["first_timestamp"] is None or timestamp < record["first_timestamp"]:
        record["first_timestamp"] = timestamp

    if record["last_timestamp"] is None or timestamp > record["last_timestamp"]:
        record["last_timestamp"] = timestamp


def merge_application_payload(record, payload):
    put_if_empty(record, "processor_id", payload.get("processor_id"))

    put_if_empty(record, "issuer_id", payload.get("issuer_id"))

    put_if_empty(record, "transaction_id", payload.get("transaction_id"))

    put_if_empty(record, "stepup_request_id", payload.get("stepup_request_id"))

    put_if_empty(record, "verification_token", payload.get("verification_token"))

    if payload.get("status"):
        record["status"] = payload["status"]

    put_if_empty(record, "stepup_type", payload.get("stepup_type"))

    customer = payload.get("customer", {})

    put_if_empty(record["customer"], "mobile", customer.get("mobile"))

    put_if_empty(record["customer"], "email", customer.get("email"))

    merchant = payload.get("merchant", {})

    put_if_empty(record["merchant"], "name", merchant.get("name"))

    transaction = payload.get("transaction", {})

    for key in ("timestamp", "amount", "currency"):
        put_if_empty(record["transaction"], key, transaction.get(key))

    payment = payload.get("payment", {})

    put_if_empty(record["payment"], "request_card_hash", payment.get("card_hash"))

    put_if_empty(record["payment"], "expiry_month", payment.get("expiry_month"))

    put_if_empty(record["payment"], "expiry_year", payment.get("expiry_year"))

    error = payload.get("error", {})

    for key in record["error"]:
        put_if_empty(record["error"], key, error.get(key))


def merge_bank_request(record, payload):
    put_if_empty(record["bank_api"], "correlation_id", payload.get("correlation_id"))

    bank = payload.get("bank_transaction", {})

    put_if_empty(record["transaction"], "date", bank.get("date"))

    put_if_empty(record["transaction"], "time", bank.get("time"))

    put_if_empty(record["bank_api"], "transaction_reference", bank.get("reference"))

    put_if_empty(record["payment"], "bank_card_hash", bank.get("card_hash"))

    put_if_empty(record["payment"], "card_expiry", bank.get("card_expiry"))

    put_if_empty(record["otp"], "value", bank.get("otp"))

    put_if_empty(record["merchant"], "name", bank.get("merchant_name"))

    put_if_empty(record["payment"], "last4_pan", bank.get("last4_pan"))

    put_if_empty(record["transaction"], "currency", bank.get("currency"))

    put_if_empty(record["transaction"], "amount", bank.get("amount"))

    put_if_empty(record["otp"], "channel", bank.get("channel"))

    put_if_empty(record["otp"], "language", bank.get("language"))

    put_if_empty(record["customer"], "mobile", bank.get("mobile"))

    put_if_empty(record["customer"], "email", bank.get("email"))


def merge_bank_response(record, payload):
    if payload.get("success") is not None:
        record["bank_api"]["success"] = payload["success"]

    put_if_empty(record["bank_api"], "correlation_id", payload.get("correlation_id"))

    content = payload.get("content", {})

    put_if_empty(record["payment"], "bank_card_hash", content.get("card_hash"))

    put_if_empty(
        record["customer"], "client_customer_id", content.get("client_customer_id")
    )

    put_if_empty(record["customer"], "email", content.get("email"))

    put_if_empty(record["customer"], "mobile", content.get("mobile"))

    put_if_empty(record["bank_api"], "org_number", content.get("org_number"))

    put_if_empty(record["transaction"], "date", content.get("transaction_date"))

    put_if_empty(record["transaction"], "time", content.get("transaction_time"))

    put_if_empty(
        record["bank_api"],
        "transaction_reference",
        content.get("transaction_reference"),
    )

    put_if_empty(record["otp"], "channel", content.get("channel"))

    put_if_empty(record["otp"], "language", content.get("language"))

    put_if_empty(record["otp"], "template_id", content.get("template_id"))

    put_if_empty(record["otp"], "sms_message_base64", content.get("message_base64"))

    put_if_empty(record["otp"], "sms_message_decoded", content.get("message_decoded"))

    fields = content.get("message_fields", {})

    put_if_empty(record["otp"], "value", fields.get("otp"))

    put_if_empty(record["payment"], "masked_card", fields.get("masked_card"))

    put_if_empty(record["merchant"], "name", fields.get("merchant"))

    put_if_empty(record["transaction"], "currency_text", fields.get("currency_text"))

    put_if_empty(record["transaction"], "amount", fields.get("amount"))


def build_transactions(events):
    """
    VFlex events are most reliably correlated by tracker number.
    TransactionId remains inside the resulting tracker record.
    """

    records = {}

    for event in events:
        tracker = event.get("tracker_no")

        # SQL/system events are preserved in events.json but are not
        # forced into a transaction.
        if not tracker:
            continue

        if tracker not in records:
            records[tracker] = new_transaction(f"tracker:{tracker}")

        record = records[tracker]

        record["tracker_no"] = tracker
        record["tracker_type"] = event.get("tracker_type")
        record["phase"] = event.get("phase")

        update_time_range(record, event.get("timestamp"))

        record["events"].append(
            {
                "event_no": event["event_no"],
                "timestamp": event["timestamp"],
                "event_type": event["event_type"],
                "severity": event["severity"],
                "parse_status": event["parse_status"],
            }
        )

        for normalized_item in event.get("normalized_payloads", []):
            role = normalized_item.get("role")
            payload = normalized_item.get("data", {})

            if role in ("application_request", "netcetera_response"):
                merge_application_payload(record, payload)

            elif role == "bank_request":
                merge_bank_request(record, payload)

            elif role == "bank_response":
                merge_bank_response(record, payload)

        if event.get("vf_input"):
            vf_input = event["vf_input"]

            put_if_empty(record["payment"], "masked_card", vf_input.get("masked_card"))

            put_if_empty(record["payment"], "card_expiry", vf_input.get("card_expiry"))

        if event.get("bank_api"):
            put_if_empty(record["bank_api"], "url", event["bank_api"].get("url"))

            put_if_empty(
                record["bank_api"], "operation", event["bank_api"].get("operation")
            )

        if event.get("sms"):
            sms = event["sms"]

            put_if_empty(record["customer"], "mobile", sms.get("mobile"))

            put_if_empty(record["otp"], "sms_xml_message", sms.get("message_text"))

            message_fields = sms.get("message_fields", {})

            put_if_empty(record["otp"], "value", message_fields.get("otp"))

            put_if_empty(
                record["payment"], "masked_card", message_fields.get("masked_card")
            )

            put_if_empty(record["merchant"], "name", message_fields.get("merchant"))

            put_if_empty(
                record["transaction"],
                "currency_text",
                message_fields.get("currency_text"),
            )

            put_if_empty(record["transaction"], "amount", message_fields.get("amount"))

        if event.get("queue"):
            queue = event["queue"]

            put_if_empty(record["queue"], "name", queue.get("name"))

            put_if_empty(record["queue"], "message_id", queue.get("message_id"))

            record["queue"]["queued"] = True

        if event["event_type"] == "otp_success":
            record["otp"]["processed_successfully"] = True

    output = list(records.values())

    for record in output:
        warnings = []

        if record["phase"] == "STEP_UP":
            if not record["transaction_id"]:
                warnings.append("Step-up flow has no TransactionId")

            if record["bank_api"]["success"] is not True:
                warnings.append("Step-up flow has no successful bank API confirmation")

            if record["status"] != "SUCCESS":
                warnings.append("Step-up flow has no successful Netcetera response")

        if record["phase"] == "INITIATE_ACTION":
            if not record["otp"]["value"]:
                warnings.append("OTP value was not extracted")

            if not record["queue"]["queued"]:
                warnings.append("No SMS queue confirmation")

            if not record["otp"]["processed_successfully"]:
                warnings.append("No OTP processed-success confirmation")

            if record["bank_api"]["success"] is not True:
                warnings.append("No successful bank OTP API confirmation")

        if record["bank_api"]["success"] is False:
            warnings.append("Bank API explicitly returned success=false")

        if any(record["error"].values()):
            warnings.append("Netcetera response contains error information")

        record["warnings"] = warnings
        record["integrity_status"] = "CHECK" if warnings else "OK"

    return output


# ============================================================
# Summary
# ============================================================


def build_summary(events, transactions):
    event_types = defaultdict(int)
    severities = defaultdict(int)
    parse_statuses = defaultdict(int)
    tracker_types = defaultdict(int)

    issuers = defaultdict(int)
    merchants = defaultdict(int)
    channels = defaultdict(int)
    currencies = defaultdict(int)
    bank_operations = defaultdict(int)
    final_statuses = defaultdict(int)

    for event in events:
        event_types[event["event_type"]] += 1
        severities[event["severity"]] += 1
        parse_statuses[event["parse_status"]] += 1

        tracker_type = event.get("tracker_type") or "SYSTEM"

        tracker_types[tracker_type] += 1

    for record in transactions:
        issuers[record.get("issuer_id") or "UNKNOWN"] += 1

        merchants[record.get("merchant", {}).get("name") or "UNKNOWN"] += 1

        channels[record.get("otp", {}).get("channel") or "UNKNOWN"] += 1

        currencies[record.get("transaction", {}).get("currency") or "UNKNOWN"] += 1

        bank_operations[record.get("bank_api", {}).get("operation") or "UNKNOWN"] += 1

        final_statuses[
            record.get("status") or record.get("integrity_status") or "UNKNOWN"
        ] += 1

    return {
        "total_events": len(events),
        "total_tracker_records": len(transactions),
        "event_type_counts": dict(sorted(event_types.items())),
        "severity_counts": dict(sorted(severities.items())),
        "parse_status_counts": dict(sorted(parse_statuses.items())),
        "tracker_type_counts": dict(sorted(tracker_types.items())),
        "issuer_counts": dict(sorted(issuers.items())),
        "channel_counts": dict(sorted(channels.items())),
        "currency_counts": dict(sorted(currencies.items())),
        "bank_operation_counts": dict(sorted(bank_operations.items())),
        "final_status_counts": dict(sorted(final_statuses.items())),
        "top_merchants": dict(
            sorted(merchants.items(), key=lambda item: item[1], reverse=True)[:30]
        ),
        "otp_processed_successfully": sum(
            1 for record in transactions if record["otp"]["processed_successfully"]
        ),
        "sms_queue_confirmed": sum(
            1 for record in transactions if record["queue"]["queued"]
        ),
        "bank_api_successful": sum(
            1 for record in transactions if record["bank_api"]["success"] is True
        ),
        "records_requiring_check": sum(
            1 for record in transactions if record["integrity_status"] == "CHECK"
        ),
    }


# ============================================================
# Top-level file parser
# ============================================================


def parse_vflex_file(input_file):
    raw_events = load_logical_events(input_file)

    events = []

    for event_number, raw_event in enumerate(raw_events, start=1):
        events.append(
            parse_logical_event(
                raw_event=raw_event,
                source_file=Path(input_file).name,
                event_number=event_number,
            )
        )

    transactions = build_transactions(events)
    summary = build_summary(events, transactions)

    errors = [event for event in events if event["severity"] == "ERROR"]

    partial_events = [event for event in events if event["parse_status"] != "parsed"]

    return (events, transactions, errors, partial_events, summary)


def save_json(output_file, value):
    with Path(output_file).open("w", encoding="utf-8") as file:
        json.dump(value, file, indent=4, ensure_ascii=False)


# ---------------------------------------------------------------------------
# LLens integration -- everything above is unmodified from the provided
# script (only the argparse-based CLI / main()/__main__ block was dropped,
# consistent with the other custom parsers in this directory).
#
# parse_vflex_file() already takes a single file path and returns
# (events, transactions, errors, partial_events, summary). events is the
# flat per-line stream; transactions is the cross-event correlation keyed by
# tracker number (see build_transactions()'s own docstring -- VFlex events
# are correlated by tracker, not TransactionId, since TransactionId isn't
# always present). errors/partial_events are already derivable from each
# event's own severity/parse_status, so the adapter doesn't re-surface them
# separately -- every event (parsed, partial, or error; tracked or
# untracked/system) is still emitted, consistent with every other parser in
# this directory never dropping a line.
# ---------------------------------------------------------------------------

DISPLAY_NAME = "VFlex StepUp/Bank API/OTP Log (Transaction Correlation)"
DEFAULT_SOURCE_SYSTEM = "vflex_transaction_log"


def detect(sample_text: str) -> bool:
    if not TIMESTAMP_RE.search(sample_text):
        return False

    lowered = sample_text.lower()
    distinctive_markers = (
        "stepupcall vf input message",
        "get data from bank payload",
        "bank api url",
        "success response from bank api call",
        "error response from bank api call",
        "sql connection established success",
    )
    return any(marker in lowered for marker in distinctive_markers)


def parse_log_file(log_file_path, output_json_path=None):
    """Adapter for the LLens custom-parser registry. Calls the original,
    unmodified parse_vflex_file() above and flattens its
    (events, transactions, ...) output into the flat record shape the
    registry expects (see custom_parser_registry.py)."""
    events, transactions, _errors, _partial_events, _summary = parse_vflex_file(log_file_path)

    tx_by_event_no = {}
    for tx in transactions:
        for ref in tx.get("events", []):
            tx_by_event_no[ref["event_no"]] = tx

    out_records = []
    for event in events:
        tx = tx_by_event_no.get(event["event_no"])
        correlation_id = (tx.get("transaction_id") if tx else None) or event.get("tracker_no")

        details = {
            "event_type": event.get("event_type"),
            "phase": event.get("phase"),
            "parse_status": event.get("parse_status"),
            "warnings": event.get("warnings"),
            "vf_input": event.get("vf_input"),
            "bank_api": event.get("bank_api"),
            "sms": event.get("sms"),
            "queue": event.get("queue"),
            "observables": event.get("observables"),
            "normalized_payloads": event.get("normalized_payloads"),
        }
        if tx:
            details["transaction"] = {
                "record_key": tx.get("record_key"),
                "transaction_id": tx.get("transaction_id"),
                "tracker_no": tx.get("tracker_no"),
                "phase": tx.get("phase"),
                "processor_id": tx.get("processor_id"),
                "issuer_id": tx.get("issuer_id"),
                "stepup_request_id": tx.get("stepup_request_id"),
                "status": tx.get("status"),
                "customer": tx.get("customer"),
                "payment": tx.get("payment"),
                "merchant": tx.get("merchant"),
                "transaction": resolve_transaction_currency(tx.get("transaction")),
                "bank_api": tx.get("bank_api"),
                "otp": tx.get("otp"),
                "queue": tx.get("queue"),
                "error": tx.get("error"),
                "integrity_status": tx.get("integrity_status"),
                "warnings": tx.get("warnings"),
            }

        out_records.append(
            {
                "timestamp": event.get("timestamp"),
                "log_level": event.get("severity") or "INFO",
                "correlation_id": correlation_id,
                "log_type": event.get("event_type"),
                "action": event.get("message") or "(no message)",
                "details": details,
                "_raw_block": event.get("raw"),
            }
        )

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(out_records, f, indent=2, default=str)

    return out_records
