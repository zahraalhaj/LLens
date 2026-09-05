# ILA Bank application logs.
#
# Adapted from the customer-provided lossless_log_parser.py ("Loss-preserving,
# multi-format application log parser") -- "loss-preserving" describes the
# technique, not the log source: every byte of the uploaded file stays
# recoverable from the parsed output (see the LLens integration banner at the
# bottom, and byte_exact_roundtrip in parse_file()'s metadata). The ILA Bank
# streams this handles are ISO-8601 + bracketed-level application logs,
# tracker-correlated where "Log Tracker No:" is present.
#
# Everything down to the "LLens integration" banner is the provided script
# verbatim, with the two changes this directory's other adapters make:
#   1. The argparse-based CLI (main() / the __main__ block) was dropped --
#      this now runs as a library module -- and `argparse` was dropped from
#      the import line with it, since nothing else used it.
#   2. Nothing else. parse_file() and report() are untouched and remain
#      callable on their own, so the standalone JSON + markdown report
#      workflow still works if it's ever wanted.
#
# The registry contract (DISPLAY_NAME / DEFAULT_SOURCE_SYSTEM / detect() /
# parse_log_file()) is implemented at the bottom, on top of the unmodified
# parse_file(), the same way parser_VFlex.py wraps parse_vflex_file().
"""Loss-preserving, multi-format application log parser."""
from __future__ import annotations
import hashlib, ipaddress, json, re
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

HEADER_PATTERNS = [
    ("tracker_log", re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\s+(?P<tz>Z|[+-]\d{2}:?\d{2}))"
        r"\s+\[(?P<level>[A-Za-z]+)\]\s+Log Tracker No:\s*(?P<tracker>[^\s]+)\s*=>\s*(?P<message>.*)$")),
    ("bracket_level_log", re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:\s+(?P<tz>Z|[+-]\d{2}:?\d{2}))?)"
        r"\s+\[(?P<level>[A-Za-z]+)\]\s*(?P<message>.*)$")),
]
TS_FORMATS = ["%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S,%f %z", "%Y-%m-%dT%H:%M:%S.%f %z",
              "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S %z"]
IP_PORT_RE = re.compile(r"(?<![\w.])(?P<ip>(?:\d{1,3}\.){3}\d{1,3})(?::(?P<port>\d{1,5}))?")
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b")
STACK_RE = re.compile(r"^\s*at\s+(?P<method>.+?)(?:\s+in\s+(?P<file>.+?):line\s+(?P<line>[^\s}]+))?\s*\}?\s*$")
EXC_RE = re.compile(r"\b(?:[A-Za-z_]\w*\.)*(?P<type>[A-Za-z_]\w*(?:Exception|Error))\b")
HTTP_RE = re.compile(r"\((?P<code>\d{3})\)\s*(?P<reason>[^.\r\n]+)")
DURATION_RE = re.compile(r"\b(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})(?:\.(?P<fraction>[0-9]+))?")
KEY_RE = re.compile(r"(?<!\w)(?P<key>[A-Za-z_][A-Za-z0-9_ ./-]{0,60}?)\s*=\s*(?P<value>.*?)(?=,\s*[A-Za-z_][A-Za-z0-9_ ./-]{0,60}?\s*=|\)\s*$|$)")
ID_KEYS = re.compile(r"(?i)(request|reqref|transaction|tranreference|correlation|trace|message|session|sequence|customer|cif|pci|msisdn).*?(id|no|number|reference)?$")
SENSITIVE_KEYS = re.compile(r"(?i)(name|email|phone|mobile|msisdn|customer|cif|pci|card|account|pan)")
SERVICE_RE = re.compile(r"^(?P<service>[A-Za-z][A-Za-z0-9_]*(?:\s+[A-Za-z][A-Za-z0-9_]*)?)\s+(?:Inputs-:|Input Message|VP_Response|V\+ Response Dump|End of request|:)")

@dataclass
class Entry:
    sequence: int
    start_line: int
    end_line: int
    raw: str
    raw_sha256: str
    format: str
    parse_status: str
    timestamp_original: Optional[str] = None
    timestamp_normalized: Optional[str] = None
    timezone: Optional[str] = None
    timestamp_precision_digits: Optional[int] = None
    level: Optional[str] = None
    logger: Optional[str] = None
    component: Optional[str] = None
    thread_id: Optional[str] = None
    process_id: Optional[str] = None
    tracker_id: Optional[str] = None
    request_ids: Optional[dict[str, list[str]]] = None
    transaction_ids: Optional[dict[str, list[str]]] = None
    other_identifiers: Optional[dict[str, list[str]]] = None
    source_destination: Optional[dict[str, Any]] = None
    ip_addresses: Optional[list[dict[str, Any]]] = None
    service: Optional[str] = None
    event_type: Optional[str] = None
    message: Optional[str] = None
    multiline_content: Optional[str] = None
    key_values: Optional[dict[str, list[str]]] = None
    status_codes: Optional[list[dict[str, Any]]] = None
    durations: Optional[list[dict[str, Any]]] = None
    exceptions: Optional[list[str]] = None
    stack_frames: Optional[list[dict[str, Any]]] = None
    embedded_json: Optional[list[Any]] = None
    embedded_xml: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    unparsed_content: Optional[str] = None


def decode_bytes(data: bytes) -> tuple[str, str, bool]:
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try: return data.decode(enc), enc, True
        except UnicodeDecodeError: pass
    return data.decode("utf-8", errors="surrogateescape"), "utf-8+surrogateescape", False

def parse_timestamp(ts: str) -> tuple[Optional[str], Optional[str], Optional[int], Optional[str]]:
    compact = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", ts.replace("Z", "+00:00"))
    precision = len(m.group(1)) if (m := re.search(r"[.,](\d+)", ts)) else 0
    tz = m.group(1) if (m := re.search(r"(Z|[+-]\d{2}:?\d{2})$", ts)) else None
    for fmt in TS_FORMATS:
        try:
            dt = datetime.strptime(compact, fmt)
            normalized = dt.isoformat(timespec="microseconds")
            return normalized, tz, precision, None
        except ValueError: pass
    return None, tz, precision, "timestamp matched header but datetime parsing failed"

def split_entries(text: str) -> list[tuple[int,int,str,Optional[re.Match],str]]:
    lines = text.splitlines(keepends=True)
    out=[]; start=0; match=None; fmt="unknown"
    for i,line in enumerate(lines):
        hm=None; hf="unknown"
        clean=line.rstrip("\r\n")
        for name,pat in HEADER_PATTERNS:
            if (m:=pat.match(clean)): hm=m; hf=name; break
        if hm:
            if i>start: out.append((start+1,i,"".join(lines[start:i]),match,fmt))
            start=i; match=hm; fmt=hf
    if start < len(lines): out.append((start+1,len(lines),"".join(lines[start:]),match,fmt))
    if not out and text=="": return []
    return out

def balanced_json(s: str) -> list[Any]:
    values=[]; dec=json.JSONDecoder(); i=0
    while i<len(s):
        j=min([x for x in (s.find('{',i),s.find('[',i)) if x>=0], default=-1)
        if j<0: break
        try: obj,end=dec.raw_decode(s[j:]); values.append(obj); i=j+end
        except json.JSONDecodeError: i=j+1
    return values

def embedded_xml(s: str) -> list[str]:
    # Conservative: only capture obvious complete XML blocks.
    vals=[]
    for m in re.finditer(r"<([A-Za-z_][\w:.-]*)(?:\s[^<>]*?)?>.*?</\1\s*>",s,re.S): vals.append(m.group(0))
    return vals

def classify_event(msg: str) -> str:
    x=msg.lower()
    if "exception" in x or " error" in x or x.startswith("error"): return "error"
    if "warning" in x or "warrning" in x: return "warning_message"
    if "end of request" in x: return "request_end"
    if "vp_response" in x or "response dump" in x or "response:" in x: return "response"
    if "input message" in x or "inputs-:" in x: return "request"
    if "retry" in x: return "retry"
    return "message"

def parse_entry(seq:int,start:int,end:int,raw:str,match:Optional[re.Match],fmt:str)->Entry:
    sha=hashlib.sha256(raw.encode("utf-8",errors="surrogatepass")).hexdigest()
    if not match:
        return Entry(seq,start,end,raw,sha,"unknown","unrecognized",message=raw.rstrip("\r\n"),unparsed_content=raw)
    gd=match.groupdict(); first,*rest=raw.splitlines()
    msg=gd.get("message") or ""; multi="\n".join(rest) if rest else None
    full=msg + (("\n"+multi) if multi else "")
    norm,tz,prec,ts_err=parse_timestamp(gd["ts"])
    kv:dict[str,list[str]]=defaultdict(list)
    for m in KEY_RE.finditer(full): kv[m.group("key").strip()].append(m.group("value").strip())
    ips=[]
    for m in IP_PORT_RE.finditer(full):
        try:
            ipaddress.ip_address(m.group("ip")); port=int(m.group("port")) if m.group("port") else None
            if port is None or port<=65535: ips.append({"ip":m.group("ip"),"port":port,"original":m.group(0)})
        except ValueError: pass
    exc=list(dict.fromkeys(m.group(0) for m in EXC_RE.finditer(full)))
    frames=[]
    for line in full.splitlines():
        if m:=STACK_RE.match(line): frames.append({k:v for k,v in m.groupdict().items() if v is not None})
    statuses=[]
    for m in HTTP_RE.finditer(full): statuses.append({"type":"http","code":int(m.group("code")),"reason":m.group("reason").strip(),"source":"explicit"})
    durations=[]
    if "end of request" in full.lower() or re.search(r"(?i)\b(duration|latency|elapsed|response time|processing time|timeout)\b",full):
        for m in DURATION_RE.finditer(full):
            frac=m.group("fraction") or ""; val=(int(m.group('h'))*3600+int(m.group('m'))*60+int(m.group('s')))*1000
            if frac: val += int((frac+"000")[:3])
            durations.append({"original":m.group(0),"milliseconds":val,"source":"explicit","precision_digits":len(frac)})
    ids_req=defaultdict(list); ids_tx=defaultdict(list); ids_other=defaultdict(list)
    for k,vals in kv.items():
        kl=k.lower()
        target=ids_tx if ("tran" in kl or "transaction" in kl) else ids_req if ("req" in kl or "correlation" in kl or "trace" in kl) else ids_other if ID_KEYS.search(k) else None
        if target is not None:
            for v in vals:
                if v not in target[k]: target[k].append(v)
    for u in UUID_RE.findall(full):
        if u not in ids_other["uuid"]: ids_other["uuid"].append(u)
    service=(m.group("service").strip() if (m:=SERVICE_RE.match(msg)) else None)
    comp=None
    if not service and (m:=re.match(r"(?P<c>[A-Za-z_][\w.]*)\s*:",msg)): comp=m.group("c")
    metadata={"continuation_line_count":len(rest),"contains_sensitive_field_names":any(SENSITIVE_KEYS.search(k) for k in kv)}
    return Entry(seq,start,end,raw,sha,fmt,"partial" if ts_err else "complete",
                 gd["ts"],norm,tz,prec,gd.get("level","").upper() or None,None,comp,None,None,gd.get("tracker"),
                 dict(ids_req) or None,dict(ids_tx) or None,dict(ids_other) or None,None,ips or None,service,
                 classify_event(full),msg,multi,dict(kv) or None,statuses or None,durations or None,exc or None,
                 frames or None,balanced_json(full) or None,embedded_xml(full) or None,metadata,ts_err)

def missing_counts(entries:list[dict[str,Any]])->dict[str,int]:
    fields=["timestamp_original","level","tracker_id","service","request_ids","transaction_ids","ip_addresses","durations","exceptions"]
    return {f:sum(e.get(f) in (None,{},[]) for e in entries) for f in fields}

def parse_file(path:Path)->dict[str,Any]:
    data=path.read_bytes(); text,encoding,strict=decode_bytes(data)
    chunks=split_entries(text); entries=[]
    for i,(s,e,raw,m,f) in enumerate(chunks,1): entries.append(asdict(parse_entry(i,s,e,raw,m,f)))
    levels=Counter(e["level"] for e in entries if e["level"])
    formats=Counter(e["format"] for e in entries)
    times=[e["timestamp_normalized"] for e in entries if e["timestamp_normalized"]]
    trackers=defaultdict(list); secondary=defaultdict(set)
    for e in entries:
        if e["tracker_id"]: trackers[e["tracker_id"]].append(e["sequence"])
        for group in (e["request_ids"] or {},e["transaction_ids"] or {}):
            for k,vals in group.items():
                for v in vals: secondary[f"{k}={v}"].add(e["sequence"])
    tx=[{"tracker_id":k,"entry_sequences":v,"event_types":[entries[n-1]["event_type"] for n in v],
         "first_timestamp":next((entries[n-1]["timestamp_normalized"] for n in v if entries[n-1]["timestamp_normalized"]),None),
         "last_timestamp":next((entries[n-1]["timestamp_normalized"] for n in reversed(v) if entries[n-1]["timestamp_normalized"]),None)} for k,v in trackers.items()]
    reconstructed="".join(e["raw"] for e in entries).encode("utf-8",errors="surrogatepass")
    stats={
      "total_input_bytes":len(data),"total_lines":len(text.splitlines()),"physical_newline_count":len(re.findall(r"\r\n|\n|\r",text)),
      "total_entries":len(entries),"fully_parsed_entries":sum(e["parse_status"]=="complete" for e in entries),
      "partially_parsed_entries":sum(e["parse_status"]=="partial" for e in entries),
      "unrecognized_entries":sum(e["parse_status"]=="unrecognized" for e in entries),
      "multiline_entries":sum(bool(e["multiline_content"]) for e in entries),"detected_format_count":len(formats),
      "format_counts":dict(formats),"level_counts":dict(levels),"errors":sum(e["level"] in ("ERR","ERROR","FATAL") or e["event_type"]=="error" for e in entries),
      "warnings":sum(e["level"] in ("WRN","WARN","WARNING") or e["event_type"]=="warning_message" for e in entries),
      "unique_tracker_ids":len(trackers),"unique_secondary_correlation_values":len(secondary),"missing_field_counts":missing_counts(entries)
    }
    return {"metadata":{"source_file":path.name,"source_path":str(path),"encoding":encoding,"strict_decode":strict,
      "source_sha256":hashlib.sha256(data).hexdigest(),"reconstructed_text_sha256":hashlib.sha256(reconstructed).hexdigest(),
      "byte_exact_roundtrip":data==reconstructed,"timestamp_range":{"first":min(times) if times else None,"last":max(times) if times else None},
      "detected_formats":list(formats),"statistics":stats},"entries":entries,"transactions":tx,
      "secondary_correlations":{k:sorted(v) for k,v in secondary.items() if len(v)>1}}

def report(doc:dict[str,Any])->str:
    m=doc["metadata"]; s=m["statistics"]; formats=m["detected_formats"]
    notes=[]
    for f in formats:
        if f=="tracker_log": notes.append("`timestamp [LEVEL] Log Tracker No: <id> => message`, with tracker-based correlation")
        elif f=="bracket_level_log": notes.append("`timestamp [LEVEL] message`")
        else: notes.append("unknown/preamble content preserved as unrecognized entries")
    return f"""# Parsing report: {m['source_file']}

## Validation summary
- Input bytes: {s['total_input_bytes']:,}
- Physical lines: {s['total_lines']:,}; newline sequences: {s['physical_newline_count']:,}
- Parsed entries: {s['total_entries']:,} (complete {s['fully_parsed_entries']:,}, partial {s['partially_parsed_entries']:,}, unrecognized {s['unrecognized_entries']:,})
- Multiline entries: {s['multiline_entries']:,}
- Detected formats: {s['detected_format_count']} ({', '.join(formats)})
- Levels: {json.dumps(s['level_counts'],ensure_ascii=False)}
- Error entries: {s['errors']:,}; warning entries/messages: {s['warnings']:,}
- Unique tracker IDs: {s['unique_tracker_ids']:,}; unique extracted secondary correlation values: {s['unique_secondary_correlation_values']:,}
- Timestamp range: {m['timestamp_range']['first']} to {m['timestamp_range']['last']}
- SHA-256: `{m['source_sha256']}`
- Byte-exact round trip from concatenated entry `raw`: **{m['byte_exact_roundtrip']}**

## Detected structures
{chr(10).join('- '+n for n in notes)}

## Parsing behavior
- A new entry starts only when a recognized timestamp/level header is found. All following continuation, blank, payload, and stack-trace lines stay attached until the next recognized header.
- Original timestamp text, timezone text, and fractional precision are preserved. A normalized ISO timestamp is additionally supplied without replacing the original.
- Key/value pairs, valid IP addresses/ports, UUIDs, HTTP codes, explicit timing strings, exception types, and .NET-style stack frames are extracted conservatively.
- `tracker_id` is the primary transaction grouping key. Extracted request/transaction key-values create secondary correlations only when the exact value repeats.
- Durations are marked `source: explicit`. No start/end-based duration is calculated by this parser unless separately implemented with an application-safe rule.
- Fixed-width and otherwise unclassified payloads remain in `message`, `multiline_content`, and `raw`; unknown headerless content is retained in `unparsed_content`.
- Values containing masking or corruption characters are preserved exactly and are not repaired or guessed.

## Ambiguities and limitations
- Field meaning is inferred only from explicit names or unmistakable syntax. Unlabeled fixed-width response positions are not assigned semantic names.
- A numeric code is classified as HTTP only when written in an HTTP-error phrase such as `(500) Internal Server Error`.
- `warning_message` includes misspelled warning text even when the formal log level is informational; level and event classification remain separate.
- Presence of sensitive-looking field names is flagged in metadata; output is not automatically redacted because lossless preservation was required.

## Missing-field counts
```json
{json.dumps(s['missing_field_counts'],indent=2)}
```
"""


# ---------------------------------------------------------------------------
# LLens integration -- everything above is the provided script (see this
# file's header comment for the two changes made to it).
#
# parse_file() already takes a single path and returns
# {metadata, entries, transactions, secondary_correlations}. `entries` is
# the flat per-entry stream (one entry = one recognized header line plus
# every continuation/payload/stack-trace line under it); `transactions` is
# the cross-entry correlation keyed by tracker id. The adapter below
# flattens that into the flat record shape the registry expects (see
# custom_parser_registry.py) without dropping an entry -- unrecognized
# headerless content is emitted too, exactly as this parser preserves it,
# consistent with every other parser in this directory never dropping a line.
# ---------------------------------------------------------------------------

DISPLAY_NAME = "ILA Bank Application Log (Tracker + Byte-Exact Capture)"
DEFAULT_SOURCE_SYSTEM = "ila_bank_app_log"

# Serilog-style abbreviated levels ("[INF]", "[WRN]", "[VRB]") that this
# parser's `[A-Za-z]+` level group accepts but backend.core.schema's
# normalize_level() maps to UNKNOWN (it knows INFO/WARN/DEBUG, plus "ERR"
# and "FATAL", but not INF/WRN/VRB/FTL). Expanded here in the adapter, not
# in schema.py, because it's this format's spelling convention rather than
# a global one.
_LEVEL_ALIASES = {
    "VRB": "TRACE", "VERBOSE": "TRACE", "DBG": "DEBUG",
    "INF": "INFO", "WRN": "WARN", "FTL": "FATAL",
}

# The header shapes this parser splits on, as a detection probe. Built from
# HEADER_PATTERNS itself so detect() can never drift from what
# split_entries() actually recognizes.
_HEADER_PROBES = [(name, re.compile(pat.pattern, re.MULTILINE)) for name, pat in HEADER_PATTERNS]

# This parser's header patterns (ISO-8601 timestamp + bracketed level, with
# or without "Log Tracker No:") are exactly the shape parser_ASBB_MW_Credit
# already claims, so detect() would otherwise collide with it on every ASBB
# MW file and be resolved by the registry's field-yield tie-break -- which
# this parser tends to win purely because it extracts more incidental
# fields, not because it understands ISO8583 credit postings better. Its
# real value is the entries ASBB MW's four cases have no branch for
# (multi-line stack traces, JSON payloads, bare key=value diagnostics), so
# it defers when the sample carries ASBB MW's own case markers -- ISO8583
# XML payloads (Case A), "Inputs (...)" function calls (Case B), or the
# "Warrning" spelling (Case C). It stays selectable manually from the
# profile picker either way, for when a byte-exact record of a file that
# does carry those markers is what's actually wanted.
_DEFERRED_TO_ASBB_MW_MARKERS = ("<?xml", "postxml>", "inputs", "warrning")


def detect(sample_text: str) -> bool:
    if not any(probe.search(sample_text) for _, probe in _HEADER_PROBES):
        return False
    lowered = sample_text.lower()
    return not any(marker in lowered for marker in _DEFERRED_TO_ASBB_MW_MARKERS)


def _correlation_id(entry: dict) -> Optional[str]:
    """tracker_id is this parser's primary grouping key; the extracted
    transaction/request key-values are its documented secondary
    correlations and stand in when a line carries no tracker (the registry
    scores a parser partly on how many records it can correlate, and more
    importantly an uncorrelated event is invisible to the app's
    transaction views)."""
    if entry.get("tracker_id"):
        return entry["tracker_id"]
    for group in ("transaction_ids", "request_ids", "other_identifiers"):
        for _key, values in (entry.get(group) or {}).items():
            if values:
                return values[0]
    return None


def parse_log_file(log_file_path, output_json_path=None):
    """Adapter for the LLens custom-parser registry. Calls the original,
    unmodified parse_file() above and flattens its entries/transactions
    into the flat record shape the registry expects."""
    doc = parse_file(Path(log_file_path))
    entries = doc["entries"]

    tx_by_sequence = {}
    for tx in doc["transactions"]:
        for sequence in tx["entry_sequences"]:
            tx_by_sequence[sequence] = tx

    out_records = []
    for entry in entries:
        level = entry.get("level")
        tx = tx_by_sequence.get(entry["sequence"])

        details = {
            "format": entry.get("format"),
            "parse_status": entry.get("parse_status"),
            "event_type": entry.get("event_type"),
            "service": entry.get("service"),
            "component": entry.get("component"),
            "tracker_id": entry.get("tracker_id"),
            "request_ids": entry.get("request_ids"),
            "transaction_ids": entry.get("transaction_ids"),
            "other_identifiers": entry.get("other_identifiers"),
            "key_values": entry.get("key_values"),
            "ip_addresses": entry.get("ip_addresses"),
            "status_codes": entry.get("status_codes"),
            "durations": entry.get("durations"),
            "exceptions": entry.get("exceptions"),
            "stack_frames": entry.get("stack_frames"),
            "embedded_json": entry.get("embedded_json"),
            "embedded_xml": entry.get("embedded_xml"),
            "multiline_content": entry.get("multiline_content"),
            "unparsed_content": entry.get("unparsed_content"),
            # The provenance half of "loss-preserving": the normalized
            # timestamp is carried ALONGSIDE the original (which stays in
            # `timestamp` below), and raw_sha256 lets any stored event be
            # checked back against the source bytes it came from.
            "timestamp_normalized": entry.get("timestamp_normalized"),
            "timezone": entry.get("timezone"),
            "timestamp_precision_digits": entry.get("timestamp_precision_digits"),
            "raw_sha256": entry.get("raw_sha256"),
            "source_lines": {"start": entry.get("start_line"), "end": entry.get("end_line")},
        }
        details.update(entry.get("metadata") or {})
        if tx:
            details["transaction"] = {
                "tracker_id": tx["tracker_id"],
                "entry_count": len(tx["entry_sequences"]),
                "event_types": tx["event_types"],
                "first_timestamp": tx["first_timestamp"],
                "last_timestamp": tx["last_timestamp"],
            }

        out_records.append(
            {
                "timestamp": entry.get("timestamp_original"),
                "log_level": _LEVEL_ALIASES.get(level, level),
                "correlation_id": _correlation_id(entry),
                # UNRECOGNIZED rather than the (absent) event_type, so
                # headerless preamble/continuation content is visibly
                # flagged in the app instead of masquerading as a message.
                "log_type": (entry.get("event_type") or "").upper() or "UNRECOGNIZED",
                "action": entry.get("message") or "(no message)",
                "details": details,
                "_raw_block": entry.get("raw"),
            }
        )

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(out_records, f, indent=2, ensure_ascii=False, default=str)

    return out_records
