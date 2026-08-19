import re
import json
import html
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from collections import defaultdict


# ============================================================
# Regex patterns
# ============================================================

TIMESTAMP_RE = re.compile(
    r"\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M"
)

LOG_TRACKER_RE = re.compile(
    r"Log Tracker No:\s*(?P<tracker>[A-Z]{2}\d+)",
    re.IGNORECASE
)

ARROW_RE = re.compile(r"=>\s*(.*)", re.DOTALL)

KV_RE = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]+)\s*=\s*(?P<value>.*?)(?=\s+[A-Za-z_][A-Za-z0-9_]+\s*=|$)"
)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

IP_RE = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
)

MOBILE_RE = re.compile(
    r"\+\d{7,15}"
)

EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)


# ============================================================
# Basic helpers
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    return (
        str(value)
        .replace("\x00", "")
        .replace("﻿", "")
        .strip()
    )


def parse_timestamp(value):
    value = clean_text(value)

    try:
        return datetime.strptime(
            value,
            "%m/%d/%Y %I:%M:%S %p"
        ).isoformat(sep=" ")
    except Exception:
        return value


def to_float(value):
    value = clean_text(value)

    if value == "":
        return None

    try:
        return float(value)
    except Exception:
        return value


def safe_get(d, *keys):
    cur = d

    for key in keys:
        if not isinstance(cur, dict):
            return None

        cur = cur.get(key)

        if cur is None:
            return None

    return cur


# ============================================================
# Split file into logical timestamped events
# ============================================================

def split_timestamped_events(text):
    """
    Splits based on timestamps.

    Works for:
      8/17/2026 3:00:01 PM Log Tracker No: ...
      8/17/2026 3:00:01 PM : Msg Received-- ...
      8/17/2026 3:00:01 PM : Message for Queue=...
    """

    text = clean_text(text)
    matches = list(TIMESTAMP_RE.finditer(text))

    events = []

    if not matches:
        lines = text.splitlines()

        for idx, line in enumerate(lines, start=1):
            line = clean_text(line)

            if line:
                events.append({
                    "event_no": idx,
                    "timestamp_raw": None,
                    "timestamp": None,
                    "raw": line
                })

        return events

    for i, match in enumerate(matches):
        timestamp_raw = match.group()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)

        raw = clean_text(text[start:end])

        if raw.startswith(":"):
            raw = clean_text(raw[1:])

        events.append({
            "event_no": i + 1,
            "timestamp_raw": timestamp_raw,
            "timestamp": parse_timestamp(timestamp_raw),
            "raw": raw
        })

    return events


# ============================================================
# JSON extraction
# ============================================================

def extract_balanced_json(text):
    """
    Extract first balanced JSON object from a message.
    Handles nested braces and strings.
    """

    text = clean_text(text)
    start = text.find("{")

    if start == -1:
        return None, None

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]

        if escape:
            escape = False
            continue

        if ch == "\\":
            escape = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if not in_string:
            if ch == "{":
                depth += 1

            elif ch == "}":
                depth -= 1

                if depth == 0:
                    raw_json = text[start:i + 1]

                    try:
                        return json.loads(raw_json), raw_json
                    except Exception:
                        return None, raw_json

    return None, text[start:]


def flatten_credentials(payload):
    result = {
        "mobile": None,
        "email": None,
        "credentials": []
    }

    credentials = payload.get("Credentials")

    if not isinstance(credentials, list):
        return result

    for item in credentials:
        if not isinstance(item, dict):
            continue

        ctype = str(item.get("Type", "")).upper()
        ctext = item.get("Text")

        result["credentials"].append({
            "id": item.get("Id"),
            "type": item.get("Type"),
            "text": ctext
        })

        if "SMS" in ctype:
            result["mobile"] = result["mobile"] or ctext

        elif "EMAIL" in ctype:
            result["email"] = result["email"] or ctext

    return result


def parse_json_payload(payload):
    """
    Extracts common fields from debit/step-up/transaction JSON.
    """

    if not isinstance(payload, dict):
        return {}

    creds = flatten_credentials(payload)

    parsed = {
        "processor_id": payload.get("ProcessorId"),
        "issuer_id": payload.get("IssuerId"),
        "transaction_id": payload.get("TransactionId"),
        "stepup_request_id": payload.get("StepupRequestId"),
        "verification_token": payload.get("VerificationToken"),
        "message_version": payload.get("MessageVersion"),

        "status": payload.get("Status"),
        "stepup_type": payload.get("StepupType"),
        "language": payload.get("Language"),

        "customer": {
            "mobile": creds.get("mobile"),
            "email": creds.get("email"),
            "credentials": creds.get("credentials")
        },

        "merchant": {
            "id": safe_get(payload, "MerchantInfo", "MerchantId"),
            "name": safe_get(payload, "MerchantInfo", "MerchantName"),
            "url": safe_get(payload, "MerchantInfo", "MerchantURL"),
            "category_code": safe_get(payload, "MerchantInfo", "MerchantCategoryCode"),
            "country_code": safe_get(payload, "MerchantInfo", "MerchantCountryCode"),
            "acquirer_id": safe_get(payload, "MerchantInfo", "AcquirerId")
        },

        "transaction": {
            "amount": safe_get(payload, "TransactionInfo", "TransactionAmount"),
            "currency": safe_get(payload, "TransactionInfo", "TransactionCurrency"),
            "timestamp": safe_get(payload, "TransactionInfo", "TransactionTimeStamp"),
            "exponent": safe_get(payload, "TransactionInfo", "TransactionExponent")
        },

        "payment": {
            "card_number_hash": safe_get(payload, "PaymentInfo", "CardNumber"),
            "expiry_month": safe_get(payload, "PaymentInfo", "CardExpiryMonth"),
            "expiry_year": safe_get(payload, "PaymentInfo", "CardExpiryYear"),
            "card_type": safe_get(payload, "PaymentInfo", "CardType"),
            "card_holder_name": safe_get(payload, "PaymentInfo", "CardHolderName")
        },

        "error": payload.get("Error")
    }

    return parsed


# ============================================================
# XML extraction
# ============================================================

def fix_bad_xml_ampersands(xml_text):
    """
    Fix naked ampersands inside values:
      e& UAE App
      INFO & EGOV AUTHORITY
    """

    if not xml_text:
        return xml_text

    return re.sub(
        r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)",
        "&amp;",
        xml_text
    )


def extract_xml(text):
    decoded = html.unescape(clean_text(text))

    starts = []

    for tag in ["<Msg>", "<EmailMsg>"]:
        pos = decoded.find(tag)
        if pos != -1:
            starts.append(pos)

    if not starts:
        return None

    start = min(starts)

    if decoded[start:].startswith("<Msg>"):
        end_tag = "</Msg>"
    else:
        end_tag = "</EmailMsg>"

    end = decoded.find(end_tag, start)

    if end == -1:
        return clean_text(decoded[start:])

    return clean_text(decoded[start:end + len(end_tag)])


def extract_tag(xml_text, tag):
    match = re.search(
        rf"<{tag}>(.*?)</{tag}>",
        xml_text,
        flags=re.IGNORECASE | re.DOTALL
    )

    if not match:
        return ""

    return clean_text(html.unescape(match.group(1)))


def xml_find(root, path):
    return clean_text(root.findtext(path))


def parse_msg_xml_regex(xml_text):
    return {
        "tracker_no": extract_tag(xml_text, "mtrackingid"),
        "org": extract_tag(xml_text, "Org"),
        "type": extract_tag(xml_text, "Typ"),
        "lang": extract_tag(xml_text, "Lang"),
        "verify": extract_tag(xml_text, "Verify"),
        "mobile": extract_tag(xml_text, "Mobile"),
        "otp": extract_tag(xml_text, "OTP"),
        "masked_card": extract_tag(xml_text, "MaskedCardNo"),
        "otppan": extract_tag(xml_text, "OTPPAN"),
        "transaction": {
            "amount": to_float(extract_tag(xml_text, "TranAmount")),
            "currency": extract_tag(xml_text, "TranCurrency"),
            "date": extract_tag(xml_text, "TranDate"),
            "time": extract_tag(xml_text, "TranTime")
        },
        "merchant": {
            "id": extract_tag(xml_text, "MerchantId") or None,
            "name": extract_tag(xml_text, "MerchantName"),
            "country_code": extract_tag(xml_text, "MerchantCountryCode") or None,
            "url": extract_tag(xml_text, "MerchantURL") or None
        },
        "parse_method": "regex_xml_fallback"
    }


def parse_msg_xml(xml_text):
    xml_text = clean_text(xml_text)

    if not xml_text:
        return None, "No Msg XML found"

    try:
        fixed = fix_bad_xml_ampersands(xml_text)
        root = ET.fromstring(fixed)

        if root.tag != "Msg":
            raise ValueError(f"Expected Msg, got {root.tag}")

        parsed = {
            "tracker_no": xml_find(root, "./Header/mtrackingid"),
            "org": xml_find(root, "./Header/Org"),
            "type": xml_find(root, "./Header/Typ"),
            "lang": xml_find(root, "./Header/Lang"),
            "verify": xml_find(root, "./Header/Verify"),
            "mobile": xml_find(root, "./Header/Mobile"),
            "otp": xml_find(root, "./Body/OTP"),
            "masked_card": xml_find(root, "./Body/MaskedCardNo"),
            "otppan": xml_find(root, "./Body/OTPPAN"),
            "transaction": {
                "amount": to_float(xml_find(root, "./Body/TranAmount")),
                "currency": xml_find(root, "./Body/TranCurrency"),
                "date": xml_find(root, "./Body/TranDate"),
                "time": xml_find(root, "./Body/TranTime")
            },
            "merchant": {
                "id": xml_find(root, "./Body/MerchantId") or None,
                "name": xml_find(root, "./Body/MerchantName"),
                "country_code": xml_find(root, "./Body/MerchantCountryCode") or None,
                "url": xml_find(root, "./Body/MerchantURL") or None
            },
            "parse_method": "xml"
        }

        if not parsed["tracker_no"]:
            raise ValueError("Missing mtrackingid")

        return parsed, None

    except Exception as ex:
        parsed = parse_msg_xml_regex(xml_text)

        if parsed.get("tracker_no"):
            parsed["xml_parse_warning"] = str(ex)
            return parsed, None

        return None, str(ex)


def parse_email_xml(xml_text):
    xml_text = clean_text(xml_text)

    if not xml_text:
        return None, "No Email XML found"

    try:
        fixed = fix_bad_xml_ampersands(xml_text)
        root = ET.fromstring(fixed)

        if root.tag != "EmailMsg":
            raise ValueError(f"Expected EmailMsg, got {root.tag}")

        parsed = {
            "org": xml_find(root, "./Header/Org"),
            "email_to": xml_find(root, "./Header/EmailTo"),
            "type": xml_find(root, "./Header/Typ"),
            "lang": xml_find(root, "./Header/Lang"),
            "verify": xml_find(root, "./Header/Verify"),
            "otp": xml_find(root, "./Body/EMAILBODY1/MWTEXT/OTP"),
            "masked_card": xml_find(root, "./Body/EMAILBODY1/MWTEXT/MaskedCardNo"),
            "otppan": xml_find(root, "./Body/EMAILBODY1/MWTEXT/OTPPAN"),
            "transaction": {
                "amount": to_float(xml_find(root, "./Body/EMAILBODY1/MWTEXT/TranAmount")),
                "currency": xml_find(root, "./Body/EMAILBODY1/MWTEXT/TranCurrency"),
                "date": xml_find(root, "./Body/EMAILBODY1/MWTEXT/TranDate"),
                "time": xml_find(root, "./Body/EMAILBODY1/MWTEXT/TranTime")
            },
            "merchant": {
                "id": xml_find(root, "./Body/EMAILBODY1/MWTEXT/MerchantId") or None,
                "name": xml_find(root, "./Body/EMAILBODY1/MWTEXT/MerchantName"),
                "country_code": xml_find(root, "./Body/EMAILBODY1/MWTEXT/MerchantCountryCode") or None,
                "url": xml_find(root, "./Body/EMAILBODY1/MWTEXT/MerchantURL") or None
            },
            "parse_method": "xml"
        }

        return parsed, None

    except Exception as ex:
        parsed = {
            "org": extract_tag(xml_text, "Org"),
            "email_to": extract_tag(xml_text, "EmailTo"),
            "otp": extract_tag(xml_text, "OTP"),
            "masked_card": extract_tag(xml_text, "MaskedCardNo"),
            "otppan": extract_tag(xml_text, "OTPPAN"),
            "transaction": {
                "amount": to_float(extract_tag(xml_text, "TranAmount")),
                "currency": extract_tag(xml_text, "TranCurrency"),
                "date": extract_tag(xml_text, "TranDate"),
                "time": extract_tag(xml_text, "TranTime")
            },
            "merchant": {
                "id": extract_tag(xml_text, "MerchantId") or None,
                "name": extract_tag(xml_text, "MerchantName"),
                "country_code": extract_tag(xml_text, "MerchantCountryCode") or None,
                "url": extract_tag(xml_text, "MerchantURL") or None
            },
            "parse_method": "regex_xml_fallback",
            "xml_parse_warning": str(ex)
        }

        if parsed.get("email_to") or parsed.get("otp"):
            return parsed, None

        return None, str(ex)


# ============================================================
# Key/value parser
# ============================================================

def parse_key_values(text):
    result = {}

    for match in KV_RE.finditer(text):
        key = clean_text(match.group("key"))
        value = clean_text(match.group("value"))

        if key:
            result[key] = value

    if not result:
        return None

    return result


# ============================================================
# Event classification
# ============================================================

def classify_event(raw):
    decoded = html.unescape(raw)
    low = decoded.lower()

    if "request body" in low and "{" in decoded:
        return "request_body_json"

    if "stepup responce to netcetra" in low or "stepup response to netcetra" in low:
        return "netcetera_response_json"

    if "debit" in low and "request" in low and "{" in decoded:
        return "debit_request_json"

    if "debit" in low and "response" in low and "{" in decoded:
        return "debit_response_json"

    if "msg received--" in low and "<msg>" in low:
        return "msg_received_xml"

    if "sms input message" in low and "<msg>" in low:
        return "sms_input_xml"

    if "sendemail" in low and "<emailmsg>" in low:
        return "email_xml"

    if "message for queue" in low:
        return "queue"

    if "msgid:" in low:
        return "queue_msg_id"

    if "otp processed successfully" in low:
        return "otp_success"

    if "postilion" in low or "postailion" in low:
        return "postilion"

    if "error" in low or "exception" in low or "failed" in low or "timeout" in low:
        return "error"

    if "=" in raw:
        return "key_value"

    return "message"


def extract_tracker(raw):
    decoded = html.unescape(raw)

    m = LOG_TRACKER_RE.search(decoded)
    if m:
        return m.group("tracker")

    m = re.search(r"<mtrackingid>(.*?)</mtrackingid>", decoded, re.IGNORECASE)
    if m:
        return clean_text(m.group(1))

    return None


def extract_message_after_arrow(raw):
    m = ARROW_RE.search(raw)
    if m:
        return clean_text(m.group(1))

    return clean_text(raw)


def extract_queue(raw):
    m = re.search(r"Message for Queue\s*=\s*([^\s]+)", raw, re.IGNORECASE)
    if m:
        return clean_text(m.group(1))

    return None


def extract_msg_id(raw):
    m = re.search(r"MsgId:\s*([A-Za-z0-9]+)", raw)
    if m:
        return clean_text(m.group(1))

    return None


def extract_observables(raw):
    return {
        "emails": EMAIL_RE.findall(raw),
        "mobiles": MOBILE_RE.findall(raw),
        "ips": IP_RE.findall(raw),
        "urls": URL_RE.findall(raw)
    }


# ============================================================
# Parse single event
# ============================================================

def parse_event(event):
    raw = clean_text(event["raw"])
    event_type = classify_event(raw)
    tracker_no = extract_tracker(raw)
    message = extract_message_after_arrow(raw)

    parsed = {
        "event_no": event["event_no"],
        "timestamp": event["timestamp"],
        "timestamp_raw": event["timestamp_raw"],
        "tracker_no": tracker_no,
        "event_type": event_type,
        "message": message,
        "raw": raw,
        "parsed": {},
        "parse_status": "parsed"
    }

    try:
        if event_type in (
            "request_body_json",
            "netcetera_response_json",
            "debit_request_json",
            "debit_response_json"
        ):
            payload, raw_json = extract_balanced_json(raw)

            parsed["parsed"] = {
                "json": payload,
                "json_raw": raw_json,
                "normalized": parse_json_payload(payload) if isinstance(payload, dict) else {}
            }

            if not payload:
                parsed["parse_status"] = "partial"
                parsed["parse_warning"] = "JSON marker found but payload could not be decoded"

        elif event_type in ("msg_received_xml", "sms_input_xml"):
            xml_text = extract_xml(raw)
            xml_parsed, error = parse_msg_xml(xml_text)

            if error:
                parsed["parse_status"] = "partial"
                parsed["parse_warning"] = error
                parsed["parsed"] = {
                    "xml_raw": xml_text
                }
            else:
                parsed["tracker_no"] = parsed["tracker_no"] or xml_parsed.get("tracker_no")
                parsed["parsed"] = {
                    "xml": xml_parsed
                }

        elif event_type == "email_xml":
            xml_text = extract_xml(raw)
            xml_parsed, error = parse_email_xml(xml_text)

            if error:
                parsed["parse_status"] = "partial"
                parsed["parse_warning"] = error
                parsed["parsed"] = {
                    "xml_raw": xml_text
                }
            else:
                parsed["parsed"] = {
                    "xml": xml_parsed
                }

        elif event_type == "queue":
            parsed["parsed"] = {
                "queue": extract_queue(raw)
            }

        elif event_type == "queue_msg_id":
            parsed["parsed"] = {
                "msg_id": extract_msg_id(raw)
            }

        elif event_type == "otp_success":
            parsed["parsed"] = {
                "otp_processed": True
            }

        elif event_type == "key_value":
            parsed["parsed"] = {
                "fields": parse_key_values(raw)
            }

        elif event_type == "postilion":
            parsed["parsed"] = {
                "postilion_message": raw
            }

        elif event_type == "error":
            parsed["parsed"] = {
                "error_message": raw
            }

        else:
            parsed["parse_status"] = "raw_only"
            parsed["parsed"] = {}

        parsed["observables"] = extract_observables(raw)

    except Exception as ex:
        parsed["parse_status"] = "partial"
        parsed["parse_warning"] = str(ex)
        parsed["parsed"] = {}
        parsed["observables"] = extract_observables(raw)

    return parsed


# ============================================================
# Transaction grouping
# ============================================================

def empty_transaction(transaction_id=None, tracker_no=None):
    return {
        "transaction_id": transaction_id,
        "trackers": [],
        "first_timestamp": None,
        "last_timestamp": None,
        "issuer_id": None,
        "processor_id": None,
        "status": None,
        "error": None,
        "customer": {
            "mobile": None,
            "email": None
        },
        "merchant": {
            "id": None,
            "name": None,
            "url": None,
            "country_code": None,
            "category_code": None
        },
        "transaction": {
            "amount": None,
            "currency": None,
            "timestamp": None,
            "date": None,
            "time": None
        },
        "payment": {
            "card_number_hash": None,
            "expiry_month": None,
            "expiry_year": None
        },
        "queue": None,
        "msg_id": None,
        "otp_processed": False,
        "events": []
    }


def update_time_range(tx, timestamp):
    if not timestamp:
        return

    if tx["first_timestamp"] is None or timestamp < tx["first_timestamp"]:
        tx["first_timestamp"] = timestamp

    if tx["last_timestamp"] is None or timestamp > tx["last_timestamp"]:
        tx["last_timestamp"] = timestamp


def put_if_empty(obj, key, value):
    if obj.get(key) in (None, "", {}):
        if value not in (None, "", {}):
            obj[key] = value


def merge_normalized(tx, normalized):
    put_if_empty(tx, "issuer_id", normalized.get("issuer_id"))
    put_if_empty(tx, "processor_id", normalized.get("processor_id"))
    put_if_empty(tx, "status", normalized.get("status"))
    put_if_empty(tx, "error", normalized.get("error"))

    customer = normalized.get("customer", {})
    put_if_empty(tx["customer"], "mobile", customer.get("mobile"))
    put_if_empty(tx["customer"], "email", customer.get("email"))

    merchant = normalized.get("merchant", {})
    put_if_empty(tx["merchant"], "id", merchant.get("id"))
    put_if_empty(tx["merchant"], "name", merchant.get("name"))
    put_if_empty(tx["merchant"], "url", merchant.get("url"))
    put_if_empty(tx["merchant"], "country_code", merchant.get("country_code"))
    put_if_empty(tx["merchant"], "category_code", merchant.get("category_code"))

    tran = normalized.get("transaction", {})
    put_if_empty(tx["transaction"], "amount", tran.get("amount"))
    put_if_empty(tx["transaction"], "currency", tran.get("currency"))
    put_if_empty(tx["transaction"], "timestamp", tran.get("timestamp"))

    payment = normalized.get("payment", {})
    put_if_empty(tx["payment"], "card_number_hash", payment.get("card_number_hash"))
    put_if_empty(tx["payment"], "expiry_month", payment.get("expiry_month"))
    put_if_empty(tx["payment"], "expiry_year", payment.get("expiry_year"))


def merge_xml_record(tx, xml_record):
    put_if_empty(tx["customer"], "mobile", xml_record.get("mobile"))

    merchant = xml_record.get("merchant")
    if isinstance(merchant, dict):
        put_if_empty(tx["merchant"], "id", merchant.get("id"))
        put_if_empty(tx["merchant"], "name", merchant.get("name"))
        put_if_empty(tx["merchant"], "url", merchant.get("url"))
        put_if_empty(tx["merchant"], "country_code", merchant.get("country_code"))

    tran = xml_record.get("transaction", {})
    put_if_empty(tx["transaction"], "amount", tran.get("amount"))
    put_if_empty(tx["transaction"], "currency", tran.get("currency"))
    put_if_empty(tx["transaction"], "date", tran.get("date"))
    put_if_empty(tx["transaction"], "time", tran.get("time"))


def build_transactions(events):
    transactions = {}
    tracker_to_txid = {}
    tracker_only = {}

    # First pass: transaction IDs from JSON
    for e in events:
        normalized = e.get("parsed", {}).get("normalized", {})

        txid = normalized.get("transaction_id")
        tracker = e.get("tracker_no")

        if txid:
            if txid not in transactions:
                transactions[txid] = empty_transaction(transaction_id=txid)

            if tracker:
                tracker_to_txid[tracker] = txid

    # Second pass: merge events
    pending_tracker = None

    for e in events:
        tracker = e.get("tracker_no") or pending_tracker
        normalized = e.get("parsed", {}).get("normalized", {})
        txid = normalized.get("transaction_id")

        if not txid and tracker:
            txid = tracker_to_txid.get(tracker)

        if txid:
            tx = transactions.setdefault(txid, empty_transaction(transaction_id=txid))
        else:
            key = tracker or f"event_{e['event_no']}"
            tx = tracker_only.setdefault(key, empty_transaction(tracker_no=tracker))

        if tracker and tracker not in tx["trackers"]:
            tx["trackers"].append(tracker)

        update_time_range(tx, e.get("timestamp"))

        tx["events"].append({
            "event_no": e["event_no"],
            "timestamp": e["timestamp"],
            "tracker_no": e.get("tracker_no"),
            "event_type": e["event_type"],
            "parse_status": e["parse_status"]
        })

        if normalized:
            merge_normalized(tx, normalized)

        xml_record = e.get("parsed", {}).get("xml")
        if isinstance(xml_record, dict):
            if xml_record.get("tracker_no"):
                pending_tracker = xml_record.get("tracker_no")
            merge_xml_record(tx, xml_record)

        if e["event_type"] == "queue":
            q = e.get("parsed", {}).get("queue")
            if q:
                tx["queue"] = q

        elif e["event_type"] == "queue_msg_id":
            msg_id = e.get("parsed", {}).get("msg_id")
            if msg_id:
                tx["msg_id"] = msg_id

        elif e["event_type"] == "otp_success":
            tx["otp_processed"] = True

        if tracker:
            pending_tracker = tracker

    all_tx = list(transactions.values()) + list(tracker_only.values())

    for tx in all_tx:
        warnings = []

        if not tx["transaction_id"]:
            warnings.append("No TransactionId found")

        if not tx["trackers"]:
            warnings.append("No tracker found")

        if tx["queue"] is None and tx["otp_processed"]:
            warnings.append("OTP processed but no queue event found")

        elif tx["queue"] is None:
            warnings.append("No queue event found")

        tx["warnings"] = warnings
        tx["integrity_status"] = "OK" if not warnings else "CHECK"

    return all_tx


# ============================================================
# Summary
# ============================================================

def build_summary(events, transactions):
    event_counts = defaultdict(int)
    parse_status_counts = defaultdict(int)
    by_tracker_type = defaultdict(int)
    by_currency = defaultdict(int)
    by_merchant = defaultdict(int)
    by_status = defaultdict(int)

    for e in events:
        event_counts[e["event_type"]] += 1
        parse_status_counts[e["parse_status"]] += 1

        tracker = e.get("tracker_no")
        if tracker and len(tracker) >= 2:
            by_tracker_type[tracker[:2]] += 1

    for tx in transactions:
        currency = tx.get("transaction", {}).get("currency") or "UNKNOWN"
        by_currency[currency] += 1

        merchant = tx.get("merchant", {}).get("name") or "UNKNOWN"
        by_merchant[merchant] += 1

        status = tx.get("status") or tx.get("integrity_status") or "UNKNOWN"
        by_status[status] += 1

    return {
        "total_events": len(events),
        "total_transactions": len(transactions),
        "event_counts": dict(sorted(event_counts.items())),
        "parse_status_counts": dict(sorted(parse_status_counts.items())),
        "tracker_type_counts": dict(sorted(by_tracker_type.items())),
        "currency_counts": dict(sorted(by_currency.items())),
        "status_counts": dict(sorted(by_status.items())),
        "top_merchants": dict(
            sorted(
                by_merchant.items(),
                key=lambda x: x[1],
                reverse=True
            )[:30]
        )
    }


# ============================================================
# File writing
# ============================================================

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def parse_file(input_file):
    input_file = Path(input_file)

    text = input_file.read_text(
        encoding="utf-8",
        errors="replace"
    )

    raw_events = split_timestamped_events(text)
    parsed_events = [parse_event(event) for event in raw_events]
    transactions = build_transactions(parsed_events)
    summary = build_summary(parsed_events, transactions)

    failed_events = [
        e for e in parsed_events
        if e.get("parse_status") == "partial"
    ]

    return parsed_events, transactions, summary, failed_events


# ---------------------------------------------------------------------------
# LLens integration -- everything above is unmodified from the provided
# script (only the argparse-based CLI / main()/__main__ block was dropped,
# consistent with the other custom parsers in this directory; this replaces
# the earlier positional-field-index implementation of this same profile
# entirely, per user request).
#
# parse_file() returns (parsed_events, transactions, summary, failed_events)
# -- events are the flat per-line stream (like parser_AFS_Netcetera.py /
# parser_OTP_Processor.py), transactions is the cross-event correlation by
# TransactionId/tracker. The adapter below flattens the two into one list of
# flat records, attaching each event's own parsed payload plus a compact
# snapshot of its resolved transaction (not the transaction's full nested
# event list, to avoid duplicating every sibling event's data onto each
# event -- same convention as parser_AFS_Netcetera.py).
#
# parse_status "partial" (JSON/XML found but not decodable, or an exception
# during parse_event()) is surfaced as its own WARN-level record rather than
# being dropped -- consistent with every other parser in this directory.
# ---------------------------------------------------------------------------

DISPLAY_NAME = "Debit Transaction Log (JSON/XML/KV Correlation)"
DEFAULT_SOURCE_SYSTEM = "debit_transaction_log"


def detect(sample_text: str) -> bool:
    if not TIMESTAMP_RE.search(sample_text):
        return False

    decoded_lower = html.unescape(sample_text).lower()

    has_debit_marker = "debit" in decoded_lower and ("request" in decoded_lower or "response" in decoded_lower)
    has_postilion_marker = "postilion" in decoded_lower or "postailion" in decoded_lower
    has_tracker = bool(LOG_TRACKER_RE.search(sample_text))

    return has_debit_marker or has_postilion_marker or has_tracker


def parse_log_file(log_file_path, output_json_path=None):
    """Adapter for the LLens custom-parser registry. Calls the original,
    unmodified parse_file() above and flattens its (events, transactions,
    summary, failed_events) output into the flat record shape the registry
    expects (see custom_parser_registry.py)."""
    parsed_events, transactions, _summary, _failed_events = parse_file(log_file_path)

    tx_by_event_no = {}
    for tx in transactions:
        for ev_ref in tx.get("events", []):
            tx_by_event_no[ev_ref["event_no"]] = tx

    out_records = []
    for event in parsed_events:
        tx_context = tx_by_event_no.get(event["event_no"])
        correlation_id = (tx_context.get("transaction_id") if tx_context else None) or event.get("tracker_no")

        details = {
            "event_type": event.get("event_type"),
            "parse_status": event.get("parse_status"),
            "parsed": event.get("parsed"),
            "observables": event.get("observables"),
        }
        if event.get("parse_warning"):
            details["parse_warning"] = event["parse_warning"]
        if tx_context:
            details["transaction"] = {
                "transaction_id": tx_context.get("transaction_id"),
                "trackers": tx_context.get("trackers"),
                "issuer_id": tx_context.get("issuer_id"),
                "processor_id": tx_context.get("processor_id"),
                "status": tx_context.get("status"),
                "error": tx_context.get("error"),
                "customer": tx_context.get("customer"),
                "merchant": tx_context.get("merchant"),
                "transaction": tx_context.get("transaction"),
                "payment": tx_context.get("payment"),
                "queue": tx_context.get("queue"),
                "otp_processed": tx_context.get("otp_processed"),
                "integrity_status": tx_context.get("integrity_status"),
                "warnings": tx_context.get("warnings"),
            }

        if event.get("parse_status") == "partial":
            log_level = "WARN"
        elif event.get("event_type") == "error":
            log_level = "ERROR"
        else:
            log_level = "INFO"

        out_records.append(
            {
                "timestamp": event.get("timestamp"),
                "log_level": log_level,
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
