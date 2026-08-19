# Adapted from the customer-provided AFS/Netcetera transaction log parser.
# Every function below down to build_summary() is UNCHANGED from the
# original source -- only the __main__ CLI block was removed (consistent
# with the other 4 custom parsers in this directory) and a new
# parse_log_file() adapter was added at the bottom to flatten this parser's
# richer two-stage output (flat events + cross-tracker transaction
# correlation) into the flat record shape the LLens custom-parser registry
# expects (see custom_parser_registry.py). No original parsing logic was
# touched to build that adapter.
import re
import json
import html
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from collections import defaultdict

LOG_RE = re.compile(
    r"^(?P<timestamp>\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)\s+"
    r"Log Tracker No:\s+(?P<tracker>[A-Z]{2}\d+)\s+=>\s+(?P<message>.*)$"
)


def clean_text(value):
    if value is None:
        return None

    return str(value).replace("\x00", "").replace("\ufeff", "").strip()


def parse_timestamp(value):
    try:
        return datetime.strptime(value, "%m/%d/%Y %I:%M:%S %p").isoformat(sep=" ")
    except Exception:
        return value


def classify_event(message):
    msg = message.lower()

    if "request body" in msg:
        return "request_body"

    if "stepupcall v+ input message" in msg:
        return "vplus_input"

    if "stepupcall v+ response message" in msg:
        return "vplus_response"

    if "stepup responce to netcetra" in msg or "stepup response to netcetra" in msg:
        return "netcetera_response"

    if "sms input message" in msg:
        return "sms_input"

    if "sms placed in queue" in msg:
        return "sms_queue"

    if "sendemail mqemailsmg message" in msg:
        return "email_message"

    if "otp processed successfully" in msg:
        return "otp_success"

    if any(x in msg for x in ["error", "exception", "failed", "timeout"]):
        return "error"

    return "other"


def extract_json(message):
    start = message.find("{")

    if start == -1:
        return None

    text = message[start:].strip()

    try:
        return json.loads(text)
    except Exception:
        return None


def xml_to_dict(element):
    result = {}

    children = list(element)

    if not children:
        return clean_text(element.text) or ""

    for child in children:
        child_value = xml_to_dict(child)

        if child.tag in result:
            if not isinstance(result[child.tag], list):
                result[child.tag] = [result[child.tag]]

            result[child.tag].append(child_value)
        else:
            result[child.tag] = child_value

    return result


def extract_xml(message):
    decoded = html.unescape(message)

    start_positions = [decoded.find("<Msg>"), decoded.find("<EmailMsg>")]

    start_positions = [x for x in start_positions if x != -1]

    if not start_positions:
        return None

    start = min(start_positions)
    xml_text = clean_text(decoded[start:])

    try:
        root = ET.fromstring(xml_text)
        return {root.tag: xml_to_dict(root)}
    except Exception:
        return None


def get_credentials(data):
    mobile = None
    email = None

    credentials = data.get("Credentials", [])

    for item in credentials:
        ctype = str(item.get("Type", "")).upper()
        text = item.get("Text")

        if "SMS" in ctype:
            mobile = text

        elif "EMAIL" in ctype:
            email = text

    return mobile, email


def get_from_xml(xml_data, path):
    if not isinstance(xml_data, dict):
        return None

    current = xml_data

    for part in path.split("."):
        if not isinstance(current, dict):
            return None

        current = current.get(part)

        if current is None:
            return None

    return current


def parse_log(input_file):
    events = []
    failed_lines = []

    input_file = Path(input_file)

    with input_file.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            raw_line = clean_text(line)

            if not raw_line:
                continue

            match = LOG_RE.match(raw_line)

            if not match:
                failed_lines.append(
                    {
                        "line_no": line_no,
                        "raw": raw_line,
                        "reason": "Line does not match log pattern",
                    }
                )
                continue

            timestamp_raw = match.group("timestamp")
            tracker_no = match.group("tracker")
            message = clean_text(match.group("message"))
            event_type = classify_event(message)

            json_payload = extract_json(message)
            xml_payload = extract_xml(message)

            msg_id = None
            msg_id_match = re.search(r"MsgId:\s*([A-Za-z0-9]+)", message)

            if msg_id_match:
                msg_id = msg_id_match.group(1)

            event = {
                "line_no": line_no,
                "timestamp": parse_timestamp(timestamp_raw),
                "timestamp_raw": timestamp_raw,
                "tracker_no": tracker_no,
                "tracker_type": tracker_no[:2],
                "event_type": event_type,
                "message": message,
                "msg_id": msg_id,
                "json": json_payload,
                "xml": xml_payload,
                "parsed": True,
            }

            events.append(event)

    return events, failed_lines


def build_transactions(events):
    transactions = {}

    tracker_to_transaction = {}

    for event in events:
        data = event.get("json")

        if not isinstance(data, dict):
            continue

        transaction_id = data.get("TransactionId")

        if transaction_id:
            tracker_to_transaction[event["tracker_no"]] = transaction_id

            if transaction_id not in transactions:
                transactions[transaction_id] = {
                    "transaction_id": transaction_id,
                    "issuer_id": None,
                    "processor_id": None,
                    "stepup": {
                        "tracker_no": None,
                        "stepup_request_id": None,
                        "status": None,
                        "error": None,
                        "vplus_input": None,
                        "vplus_response": None,
                    },
                    "initiate_action": {
                        "tracker_no": None,
                        "stepup_request_id": None,
                        "verification_token": None,
                        "otp_processed": False,
                        "sms_queued": False,
                        "sms_msg_id": None,
                    },
                    "customer": {"mobile": None, "email": None},
                    "merchant": {
                        "name": None,
                        "id": None,
                        "url": None,
                        "country_code": None,
                        "category_code": None,
                    },
                    "transaction": {
                        "amount": None,
                        "currency": None,
                        "timestamp": None,
                    },
                    "payment": {
                        "card_number_hash": None,
                        "expiry_month": None,
                        "expiry_year": None,
                    },
                    "trackers": [],
                    "events": [],
                    "derived": {
                        "has_sms": False,
                        "has_email": False,
                        "is_success": False,
                        "has_stepup": False,
                        "has_initiate_action": False,
                    },
                }

    for event in events:
        tracker = event["tracker_no"]
        transaction_id = tracker_to_transaction.get(tracker)

        if not transaction_id:
            data = event.get("json")

            if isinstance(data, dict):
                transaction_id = data.get("TransactionId")

        if not transaction_id:
            continue

        tx = transactions[transaction_id]

        if tracker not in tx["trackers"]:
            tx["trackers"].append(tracker)

        tx["events"].append(
            {
                "line_no": event["line_no"],
                "timestamp": event["timestamp"],
                "tracker_no": tracker,
                "tracker_type": event["tracker_type"],
                "event_type": event["event_type"],
                "message": event["message"],
                "msg_id": event["msg_id"],
            }
        )

        data = event.get("json") or {}
        xml_data = event.get("xml") or {}

        if isinstance(data, dict):
            tx["issuer_id"] = tx["issuer_id"] or data.get("IssuerId")
            tx["processor_id"] = tx["processor_id"] or data.get("ProcessorId")

            if event["tracker_type"] == "SU":
                tx["stepup"]["tracker_no"] = tracker
                tx["stepup"]["stepup_request_id"] = tx["stepup"][
                    "stepup_request_id"
                ] or data.get("StepupRequestId")

            elif event["tracker_type"] == "IA":
                tx["initiate_action"]["tracker_no"] = tracker
                tx["initiate_action"]["stepup_request_id"] = tx["initiate_action"][
                    "stepup_request_id"
                ] or data.get("StepupRequestId")
                tx["initiate_action"]["verification_token"] = tx["initiate_action"][
                    "verification_token"
                ] or data.get("VerificationToken")

            if event["event_type"] == "netcetera_response":
                tx["stepup"]["status"] = data.get("Status")
                tx["stepup"]["error"] = data.get("Error")

            mobile, email = get_credentials(data)

            if mobile:
                tx["customer"]["mobile"] = tx["customer"]["mobile"] or mobile

            if email:
                tx["customer"]["email"] = tx["customer"]["email"] or email

            merchant = data.get("MerchantInfo")

            if isinstance(merchant, dict):
                tx["merchant"]["name"] = tx["merchant"]["name"] or merchant.get(
                    "MerchantName"
                )
                tx["merchant"]["id"] = tx["merchant"]["id"] or merchant.get(
                    "MerchantId"
                )
                tx["merchant"]["url"] = tx["merchant"]["url"] or merchant.get(
                    "MerchantURL"
                )
                tx["merchant"]["country_code"] = tx["merchant"][
                    "country_code"
                ] or merchant.get("MerchantCountryCode")
                tx["merchant"]["category_code"] = tx["merchant"][
                    "category_code"
                ] or merchant.get("MerchantCategoryCode")

            txn = data.get("TransactionInfo")

            if isinstance(txn, dict):
                tx["transaction"]["amount"] = tx["transaction"]["amount"] or txn.get(
                    "TransactionAmount"
                )
                tx["transaction"]["currency"] = tx["transaction"][
                    "currency"
                ] or txn.get("TransactionCurrency")
                tx["transaction"]["timestamp"] = tx["transaction"][
                    "timestamp"
                ] or txn.get("TransactionTimeStamp")

            payment = data.get("PaymentInfo")

            if isinstance(payment, dict):
                tx["payment"]["card_number_hash"] = tx["payment"][
                    "card_number_hash"
                ] or payment.get("CardNumber")
                tx["payment"]["expiry_month"] = tx["payment"][
                    "expiry_month"
                ] or payment.get("CardExpiryMonth")
                tx["payment"]["expiry_year"] = tx["payment"][
                    "expiry_year"
                ] or payment.get("CardExpiryYear")

        if event["event_type"] == "sms_input":
            mobile = get_from_xml(xml_data, "Msg.Header.Mobile")
            amount = get_from_xml(xml_data, "Msg.Body.TranAmount")
            currency = get_from_xml(xml_data, "Msg.Body.TranCurrency")
            merchant_name = get_from_xml(xml_data, "Msg.Body.MerchantName")

            if mobile:
                tx["customer"]["mobile"] = tx["customer"]["mobile"] or mobile

            if amount:
                tx["transaction"]["amount"] = tx["transaction"]["amount"] or amount

            if currency:
                tx["transaction"]["currency"] = (
                    tx["transaction"]["currency"] or currency
                )

            if merchant_name:
                tx["merchant"]["name"] = tx["merchant"]["name"] or merchant_name

        elif event["event_type"] == "email_message":
            email = get_from_xml(xml_data, "EmailMsg.Header.EmailTo")

            if email:
                tx["customer"]["email"] = tx["customer"]["email"] or email

        elif event["event_type"] == "sms_queue":
            tx["initiate_action"]["sms_queued"] = True
            tx["initiate_action"]["sms_msg_id"] = tx["initiate_action"][
                "sms_msg_id"
            ] or event.get("msg_id")

        elif event["event_type"] == "otp_success":
            tx["initiate_action"]["otp_processed"] = True

        elif event["event_type"] == "vplus_input":
            tx["stepup"]["vplus_input"] = event["message"]

        elif event["event_type"] == "vplus_response":
            tx["stepup"]["vplus_response"] = event["message"]

    for tx in transactions.values():
        tx["derived"]["has_sms"] = bool(tx["customer"]["mobile"])
        tx["derived"]["has_email"] = bool(tx["customer"]["email"])
        tx["derived"]["has_stepup"] = bool(tx["stepup"]["tracker_no"])
        tx["derived"]["has_initiate_action"] = bool(tx["initiate_action"]["tracker_no"])

        tx["derived"]["is_success"] = (
            tx["stepup"]["status"] == "SUCCESS"
            or tx["initiate_action"]["otp_processed"] is True
        )

    return list(transactions.values())


def build_summary(events, transactions, failed_lines):
    event_counts = defaultdict(int)
    issuer_counts = defaultdict(int)
    status_counts = defaultdict(int)

    for event in events:
        event_counts[event["event_type"]] += 1

    for tx in transactions:
        issuer_counts[tx.get("issuer_id") or "UNKNOWN"] += 1

        status = tx.get("stepup", {}).get("status")

        if tx["initiate_action"]["otp_processed"]:
            status = status or "OTP_PROCESSED"

        status_counts[status or "UNKNOWN"] += 1

    return {
        "total_events": len(events),
        "total_transactions": len(transactions),
        "failed_lines": len(failed_lines),
        "event_counts": dict(event_counts),
        "issuer_counts": dict(issuer_counts),
        "status_counts": dict(status_counts),
    }


# ---------------------------------------------------------------------------
# LLens integration -- everything below is new, additive code. Nothing above
# this line was modified from the provided parser.
# ---------------------------------------------------------------------------

DISPLAY_NAME = "AFS / Netcetera 3DS StepUp (Transaction Correlation)"
DEFAULT_SOURCE_SYSTEM = "afs_netcetera_3ds_stepup"

# This format shares the same "<US timestamp> Log Tracker No: <ID> => "
# convention as parser_ABCE_Credit.py, but with a more specific tracker ID
# shape ([A-Z]{2}\d+, e.g. "SU12345"/"IA67890") -- checking against the
# real LOG_RE above (not just "contains Log Tracker No:") makes this
# reasonably specific, but a genuine ambiguity with ABCE_Credit is still
# possible if an ABCE_Credit log happens to use tracker IDs in exactly this
# shape. The registry's existing detect_custom_parser() already handles
# that generically (scores every match, picks the best field-extraction
# yield, surfaces a warning on ties) -- no special-casing needed here.
def detect(sample_text: str) -> bool:
    # LOG_RE's ^/$ anchors are unqualified (no re.MULTILINE) -- that's
    # correct for its real use in parse_log(), which matches one already-
    # split line at a time, but calling .search() with the whole multi-line
    # sample blob would only match if the ENTIRE sample were one line.
    # Check line-by-line instead, matching how the parser itself consumes input.
    return any(LOG_RE.match(line) for line in sample_text.splitlines())


def parse_log_file(log_file_path, output_json_path=None):
    """Adapter for the LLens custom-parser registry. Calls the original,
    unmodified parse_log()/build_transactions() above and flattens their
    two-stage output (flat events + cross-tracker transaction correlation)
    into one list of flat records, which is the shape every custom parser
    in this directory is expected to return (see custom_parser_registry.py).

    Design choices:
    - correlation_id is the resolved TransactionId when available (ties
      together SU/IA/SMS/Email events that share one customer-facing
      transaction, which is the whole point of build_transactions()),
      falling back to the tracker_no for events that never resolved to a
      transaction (e.g. a lone SMS/email delivery log with no JSON body).
    - Each event's `details` carries a compact summary of its resolved
      transaction (not the transaction's full nested event list, to avoid
      duplicating every sibling event's data onto every single event).
    - Failed-to-parse lines are surfaced as their own WARN-level records
      (log_type="unparsed") rather than silently dropped, consistent with
      how every other ingestion path in LLens never discards a line.
    - _raw_block is reconstructed from the matched groups (timestamp +
      tracker + message) since the original parser doesn't retain the
      exact original line text. This is a faithful but not always
      byte-identical reconstruction (whitespace around "Log Tracker No:"/
      "=>" is normalized to single spaces) -- cosmetic only, doesn't affect
      any parsed field.
    """
    events, failed_lines = parse_log(log_file_path)
    transactions = build_transactions(events)
    tx_by_id = {tx["transaction_id"]: tx for tx in transactions}

    # Same one-line rule build_transactions() already uses internally to
    # link a tracker to a transaction -- re-derived here since
    # build_transactions() doesn't return the mapping itself.
    tracker_to_transaction = {}
    for event in events:
        data = event.get("json")
        if isinstance(data, dict) and data.get("TransactionId"):
            tracker_to_transaction[event["tracker_no"]] = data["TransactionId"]

    records = []

    for event in events:
        tracker = event["tracker_no"]
        txn_id = tracker_to_transaction.get(tracker)
        tx_context = tx_by_id.get(txn_id) if txn_id else None

        details = {
            "tracker_no": tracker,
            "tracker_type": event["tracker_type"],
            "msg_id": event["msg_id"],
            "json": event.get("json"),
            "xml": event.get("xml"),
        }
        if tx_context:
            details["transaction"] = {
                "transaction_id": tx_context["transaction_id"],
                "issuer_id": tx_context["issuer_id"],
                "processor_id": tx_context["processor_id"],
                "merchant": tx_context["merchant"],
                "transaction_info": tx_context["transaction"],
                "customer": tx_context["customer"],
                "derived": tx_context["derived"],
                "stepup_status": tx_context["stepup"]["status"],
                "otp_processed": tx_context["initiate_action"]["otp_processed"],
            }

        records.append(
            {
                "timestamp": event["timestamp"],  # already ISO-formatted by parse_timestamp()
                "log_level": "ERROR" if event["event_type"] == "error" else "INFO",
                "correlation_id": txn_id or tracker,
                "log_type": event["event_type"],
                "action": event["message"],
                "details": details,
                "_raw_block": f'{event["timestamp_raw"]} Log Tracker No: {tracker} => {event["message"]}',
            }
        )

    for failed in failed_lines:
        records.append(
            {
                "timestamp": None,
                "log_level": "WARN",
                "correlation_id": None,
                "log_type": "unparsed",
                "action": failed["reason"],
                "details": {"raw": failed["raw"], "line_no": failed["line_no"]},
                "_raw_block": failed["raw"],
            }
        )

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, default=str)

    return records
