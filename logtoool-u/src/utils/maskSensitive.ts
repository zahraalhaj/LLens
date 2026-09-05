/**
 * Display-layer masking for sensitive values in log content.
 *
 * WHY THIS LIVES IN THE FRONTEND
 * ------------------------------
 * Ingestion is deliberately loss-preserving: parsers keep the source bytes
 * exactly as uploaded (see backend/custom_parsers/parser_ILA_Bank.py, whose
 * report explicitly notes "output is not automatically redacted because
 * lossless preservation was required"). That is the right call for an
 * investigation tool -- you cannot re-derive a value you threw away at parse
 * time -- but it means anything rendering `raw`, `message` or `attributes`
 * is rendering unredacted card numbers, OTPs, mobiles and emails. This
 * module is the render-time guard for those surfaces.
 *
 * It masks WHAT IS SHOWN, not what is stored or queried. Search still
 * matches on the true value, and the backend export endpoint is untouched
 * (see the note in ExploreView's export handler).
 *
 * MASK STYLES MIRROR THE BACKEND
 * ------------------------------
 * The analysis layer already has masking conventions
 * (backend/analysis/normalized_schema.py: mask_mobile keeps the last 2
 * digits, mask_email keeps the first local char + full domain,
 * extract_card_last4 keeps the last 4). Those exact conventions are
 * reproduced here so a value masked by the analysis pipeline and the same
 * value masked at render time read identically -- otherwise the same card
 * would appear two different ways in two different views and look like two
 * different cards.
 *
 * The detection patterns likewise mirror backend/analysis/quality.py's
 * scan_for_sensitive_data(), which is the system's existing definition of
 * "sensitive", with one deliberate strengthening noted at PAN_RE below.
 */

export const REDACTED = '[REDACTED]';
export const MASKED = '[MASKED]';

/** Keeps the last 2 digits -- backend mask_mobile()'s convention. */
export function maskMobile(value: string): string {
  const digits = value.replace(/\D/g, '');
  if (!digits) return value;
  if (digits.length <= 2) return '*'.repeat(digits.length);
  return '*'.repeat(digits.length - 2) + digits.slice(-2);
}

/** Keeps the first local character + full domain -- backend mask_email(). */
export function maskEmail(value: string): string {
  const at = value.indexOf('@');
  if (at < 0) return MASKED;
  const local = value.slice(0, at);
  const domain = value.slice(at + 1);
  if (!domain) return MASKED;
  return `${local.slice(0, 1)}***@${domain}`;
}

/** Keeps the last 4 -- backend extract_card_last4()'s convention. */
export function maskPan(digits: string): string {
  const clean = digits.replace(/\D/g, '');
  if (clean.length <= 4) return '*'.repeat(clean.length);
  return '*'.repeat(clean.length - 4) + clean.slice(-4);
}

/**
 * Luhn check. The backend's _PAN_RE is a bare `\b(?:\d[ -]?){13,19}\b`,
 * which is fine for an audit scanner that only wants to flag "something
 * PAN-shaped is in here" -- a false positive there costs one extra finding.
 * At render time a false positive silently destroys legitimate operational
 * data: a 14-digit concatenated datetime (20260827150010) and long
 * reference/sequence numbers are all PAN-shaped. Luhn is what actually
 * separates a card number from a long number that merely looks like one.
 */
export function passesLuhn(digits: string): boolean {
  if (!/^\d{13,19}$/.test(digits)) return false;
  let sum = 0;
  let double = false;
  for (let i = digits.length - 1; i >= 0; i--) {
    let d = digits.charCodeAt(i) - 48;
    if (double) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    sum += d;
    double = !double;
  }
  return sum % 10 === 0;
}

// --- key-name based detection ------------------------------------------------

/**
 * Sensitive when one of the key's words (or two adjacent words joined) is in
 * this set. Word-splitting rather than substring matching is what stops
 * "Panel" matching "pan" and "Company" matching... nothing, but the class of
 * bug is the same.
 */
const SENSITIVE_KEY_WORDS = new Set([
  'pan', 'cardno', 'cardnum', 'cardnumber', 'creditcard', 'debitcard', 'cardholder',
  'iban', 'accountno', 'accountnum', 'accountnumber', 'acctno', 'cif', 'pci',
  'otp', 'pin', 'cvv', 'cvc', 'password', 'passwd', 'secret', 'apikey',
  'authtoken', 'accesstoken', 'verificationtoken', 'credential',
  'msisdn', 'mobile', 'phone', 'telephone', 'email', 'dob', 'dateofbirth',
  'nationalid', 'ssn', 'passport', 'address', 'name', 'customername',
  'firstname', 'lastname', 'fullname', 'username', 'customer',
]);

/**
 * Checked against the whole normalized key BEFORE the word test, for keys
 * whose words are individually sensitive but which are not PII:
 * - merchant* is business data the app deliberately shows unmasked (it drives
 *   InvestigationView's merchant filter and is a non-masked field in the
 *   analysis layer's FIELD_CONSISTENCY_CHECKS);
 * - ipaddress/ip_addresses is operational, and is a key parser_ILA_Bank emits;
 * - the rest are structural log fields whose names merely contain "name".
 */
const NEVER_SENSITIVE_KEYS = new Set([
  'merchantname', 'merchantid', 'merchanturl', 'merchantcountrycode', 'merchant',
  'ipaddress', 'ipaddresses', 'ip', 'hostname', 'filename', 'file', 'sourcefile',
  'servicename', 'service', 'methodname', 'method', 'displayname', 'queuename',
  'queue', 'classname', 'loggername', 'eventname', 'tokentype', 'nameofservice',
  'batchid', 'addressfamily',
]);

function keyWords(key: string): string[] {
  return key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .map((w) => w.toLowerCase());
}

export function isSensitiveKey(key: string): boolean {
  const normalized = key.replace(/[^A-Za-z0-9]/g, '').toLowerCase();
  if (!normalized) return false;
  if (NEVER_SENSITIVE_KEYS.has(normalized)) return false;
  if (SENSITIVE_KEY_WORDS.has(normalized)) return true;

  const words = keyWords(key);
  for (let i = 0; i < words.length; i++) {
    if (SENSITIVE_KEY_WORDS.has(words[i])) return true;
    if (i + 1 < words.length && SENSITIVE_KEY_WORDS.has(words[i] + words[i + 1])) return true;
  }
  return false;
}

/**
 * Keys whose values are ALREADY a safe derived form -- the source system (or
 * the analysis layer) masked them at source. Re-masking them destroys the
 * last-4/domain that makes them useful without protecting anything: the
 * whole point of `MaskedCardNo` is that it is already masked.
 */
const ALREADY_SAFE_KEYS = new Set([
  'maskedcardno', 'maskedcard', 'maskedpan', 'maskedmobile', 'maskedemail',
  'cardlast4', 'last4', 'otppan', 'panlast4', 'accountlast4',
]);

/** True for a value the source already masked, e.g. "400000******0002". */
function isAlreadyMasked(value: string): boolean {
  return /[*\u2022]{2,}/.test(value) || /X{4,}/i.test(value);
}

/** Masks a value that a sensitive KEY vouched for, so shape alone need not. */
export function maskKnownSensitiveValue(key: string, value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return value;
  if (ALREADY_SAFE_KEYS.has(key.replace(/[^A-Za-z0-9]/g, '').toLowerCase())) return value;
  if (isAlreadyMasked(trimmed)) return value;
  if (trimmed.includes('@')) return maskEmail(trimmed);

  const digits = trimmed.replace(/\D/g, '');
  const mostlyDigits = digits.length >= trimmed.replace(/\s/g, '').length - 3;
  if (digits && mostlyDigits) {
    // <=4 digits (OTP, CVV, PIN, an already-truncated last4) has no safe
    // partial to keep -- showing "the last 2 of 4" would leak half of it.
    if (digits.length <= 4) return '*'.repeat(digits.length);
    if (digits.length >= 13) return maskPan(digits);
    return maskMobile(digits);
  }
  return MASKED;
}

// --- free-text detection -----------------------------------------------------

const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
// Separators are matched only BETWEEN digits. The backend's trailing
// `[ -]?` is harmless for a scanner that only reads match.group(), but here
// the match is REPLACED, so a swallowed trailing space is deleted from the
// rendered line ("...0002 declined" -> "...0002declined").
const PAN_RE = /\b\d(?:[ -]?\d){12,18}\b/g;
/**
 * IBAN candidates. Shape alone is not enough: VFlex tracker numbers are
 * two letters followed by 18 digits (parser_VFlex.py's TRACKER_RE,
 * `SU100000000000000001`), which matches this pattern exactly and would be
 * destroyed -- and the tracker is the primary correlation key, so losing it
 * is worse than losing a PAN. Candidates are therefore checked against the
 * ISO country prefix and the mod-97 checksum before being masked, the same
 * shape-plus-validation rule Luhn provides for PANs.
 */
const IBAN_RE = /\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b/g;

/** ISO 3166-1 alpha-2 prefixes in the IBAN registry. "SU" is not among them. */
const IBAN_COUNTRIES = new Set([
  'AD','AE','AL','AT','AZ','BA','BE','BG','BH','BR','BY','CH','CR','CY','CZ','DE','DK','DO','EE',
  'EG','ES','FI','FO','FR','GB','GE','GI','GL','GR','GT','HR','HU','IE','IL','IQ','IS','IT','JO',
  'KW','KZ','LB','LC','LI','LT','LU','LV','LY','MC','MD','ME','MK','MR','MT','MU','NL','NO','PK',
  'PL','PS','PT','QA','RO','RS','RU','SA','SC','SD','SE','SI','SK','SM','SO','ST','SV','TL','TN',
  'TR','UA','VA','VG','XK',
]);

/** ISO 7064 mod-97-10: a valid IBAN leaves a remainder of 1. */
export function isValidIban(candidate: string): boolean {
  const value = candidate.toUpperCase();
  if (value.length < 15 || value.length > 34) return false;
  if (!IBAN_COUNTRIES.has(value.slice(0, 2))) return false;
  const rearranged = value.slice(4) + value.slice(0, 4);
  let remainder = 0;
  for (const ch of rearranged) {
    const code = ch.charCodeAt(0);
    // Letters expand to their two-digit ordinal (A=10 ... Z=35).
    const chunk = code >= 65 && code <= 90 ? String(code - 55) : ch;
    if (!/^\d+$/.test(chunk)) return false;
    for (const digit of chunk) remainder = (remainder * 10 + Number(digit)) % 97;
  }
  return remainder === 1;
}
const E164_RE = /\+\d{7,15}\b/g;
/**
 * Label-then-digits. The gap excludes `<` and `>` so the label in one XML
 * element cannot reach across into the next element's value: in
 * `<OTP>482913</OTP><OTPPAN>0002</OTPPAN>` the gap would otherwise span
 * `></OTP><OTPPAN>` and mask the already-safe last-4 in OTPPAN.
 */
const OTP_RE = /\b(otp|passcode|one[\s-]?time[\s-]?(?:code|password))\b([^0-9<>\n]{0,20})(\d{4,8})\b/gi;

/**
 * The OTP stated BEFORE its label, which is how it actually appears in a
 * dispatched SMS body: "Dear Customer, 482913 is your OTP for card
 * XXXX1234 at <merchant> for USD 150.00." -- the exact shape
 * parser_VFlex.py's OTP_MESSAGE_RE was written against, so these bodies are
 * known to be present in real logs. OTP_RE above only matches label-then-
 * digits and reads straight past this, leaving a live OTP on screen.
 *
 * A lookahead keeps the replacement to the digits themselves, and requires
 * the label within a short span so an unrelated number followed eventually
 * by the word "code" is not swallowed.
 */
const OTP_TRAILING_RE =
  /\b(\d{4,8})(?=\s+(?:is|as)\b[^.\n]{0,40}?\b(?:otp|passcode|verification\s+code|security\s+code|one[\s-]?time\s+(?:code|password|pin))\b)/gi;
/**
 * A labelled secret. The optional scheme group is the fix for
 * `Authorization: Bearer eyJhbGci...`: without it the value capture stops on
 * the word "Bearer", redacts THAT, and leaves the actual token in place --
 * the header is masked and the credential is not. The scheme is preserved so
 * the line still reads as an auth header.
 */
const SECRET_RE =
  /\b(password|passwd|secret|verificationtoken|apikey|api_key|auth_token|authorization|bearer|token)\b(["'\s:=]{1,5})((?:Bearer|Basic|Token|JWT|Digest)\s+)?([A-Za-z0-9\-_.]{6,})/gi;

/**
 * A bare JWT, with no label to key off. These reach logs constantly (an echoed
 * request header, a dumped payload) and the `eyJ` prefix -- base64 for `{"` --
 * plus two dot-separated segments is specific enough to act on alone.
 */
const JWT_RE = /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]+)?/g;

/**
 * Keys whose values legitimately CONTAIN SPACES AND COMMAS -- a person's
 * name, a postal address. These need a value pattern that runs past
 * whitespace, or masking "Jordan Testperson" leaves "Testperson" in plain
 * sight; every other sensitive key (an id, a PAN, a mobile) is a single
 * token and uses the tight pattern below, which cannot over-consume.
 */
const MULTIWORD_KEY_WORDS = new Set([
  'name', 'customername', 'firstname', 'lastname', 'fullname', 'middlename',
  'cardholder', 'cardholdername', 'address', 'customer', 'beneficiary',
]);

function isMultiwordSensitiveKey(key: string): boolean {
  if (!isSensitiveKey(key)) return false;
  const normalized = key.replace(/[^A-Za-z0-9]/g, '').toLowerCase();
  if (MULTIWORD_KEY_WORDS.has(normalized)) return true;
  return keyWords(key).some((w) => MULTIWORD_KEY_WORDS.has(w));
}

/**
 * Multi-word sensitive values: run past spaces and commas, stopping only at
 * the next ` Key=` / ` Key:`, a `<`, a `;`/`)`, or end of line. The
 * negative lookahead is what keeps this from swallowing a later sensitive
 * pair and hiding it from the pass -- same technique as the backend
 * parser's own KEY_RE (backend/custom_parsers/parser_ILA_Bank.py).
 */
const LABELED_FREEFORM_KV_RE =
  /\b([A-Za-z_][A-Za-z0-9_. -]{0,40}?)\s*([:=])\s*("[^"\n]*"|'[^'\n]*'|(?:(?!\s+[A-Za-z_][\w.-]{0,40}\s*[:=])[^;)\n<])*)/g;

/**
 * Sensitive keys whose value is a number that is routinely written in
 * space- or dash-separated groups: `Mobile: +973 3344 5566`, `CardNo: 4111
 * 1111 1111 1111`. The tight pattern below stops at the first space and
 * masks only the leading group, which leaks the rest of the number -- so
 * these keys get their own pass whose value may span separators.
 *
 * Safe to let it run past whitespace because the value pattern admits only
 * digits and number punctuation: unlike a free-text value it cannot run on
 * into the surrounding prose.
 */
const GROUPED_NUMERIC_KEY_WORDS = new Set([
  'mobile', 'phone', 'telephone', 'tel', 'msisdn', 'fax',
  'cardno', 'cardnum', 'cardnumber', 'pan', 'creditcard', 'debitcard',
  'iban', 'accountno', 'accountnumber', 'acctno',
]);

function isGroupedNumericKey(key: string): boolean {
  if (!isSensitiveKey(key)) return false;
  const normalized = key.replace(/[^A-Za-z0-9]/g, '').toLowerCase();
  if (GROUPED_NUMERIC_KEY_WORDS.has(normalized)) return true;
  const words = keyWords(key);
  return words.some((w, i) => GROUPED_NUMERIC_KEY_WORDS.has(w) || GROUPED_NUMERIC_KEY_WORDS.has(w + (words[i + 1] || '')));
}

const LABELED_GROUPED_NUMERIC_RE =
  /\b([A-Za-z_][A-Za-z0-9_. -]{0,40}?)\s*([:=])\s*(\+?[0-9][0-9 ()\u2011-]{4,30}[0-9])/g;

/**
 * Every other `Key=value` / `Key: value`. The value stops at the first
 * whitespace or separator, so trailing prose after a masked value
 * ("CIF=50021 retry scheduled") survives untouched.
 */
const LABELED_KV_RE = /\b([A-Za-z_][A-Za-z0-9_. -]{0,40}?)\s*([:=])\s*("[^"\n]*"|'[^'\n]*'|[^\s,;)&<]+)/g;

/** `<Tag>value</Tag>`, which is how the ISO8583/OTP XML payloads carry fields. */
const XML_TAG_RE = /<([A-Za-z_][\w.:-]*)>([^<>]{1,200})<\/\1>/g;

/**
 * Masks sensitive values in one piece of free log text.
 *
 * Order matters: unambiguous shapes (secrets, emails) go first, then
 * key-labelled values -- where the key vouches for the value and the shape
 * need not -- then the remaining bare shapes. Each pass replaces digits with
 * `*`, so a later pass cannot re-match and double-mask what an earlier one
 * already handled.
 */
export function maskText(text: string): string {
  if (!text) return text;
  let out = text;

  // SECRET_RE first: it knows the `<label>: <scheme> <token>` shape and keeps
  // the scheme. Running JWT_RE first would replace the token with a
  // bracketed placeholder the secret value-class cannot match, making
  // SECRET_RE backtrack onto the scheme word and redact "Bearer" instead.
  out = out.replace(SECRET_RE, (_m, label, sep, scheme) => `${label}${sep}${scheme || ''}${REDACTED}`);
  out = out.replace(JWT_RE, () => REDACTED);
  out = out.replace(EMAIL_RE, (m) => maskEmail(m));
  out = out.replace(OTP_RE, (_m, label, gap, code: string) => `${label}${gap}${'*'.repeat(code.length)}`);
  out = out.replace(OTP_TRAILING_RE, (code: string) => '*'.repeat(code.length));

  out = out.replace(XML_TAG_RE, (whole, tag: string, value: string) =>
    isSensitiveKey(tag) ? `<${tag}>${maskKnownSensitiveValue(tag, value)}</${tag}>` : whole
  );

  const replaceLabeled = (multiword: boolean) => (whole: string, key: string, sep: string, value: string) => {
    if (multiword ? !isMultiwordSensitiveKey(key) : !isSensitiveKey(key) || isMultiwordSensitiveKey(key)) {
      return whole;
    }
    const quote = value[0] === '"' || value[0] === "'" ? value[0] : '';
    const raw = quote ? value.slice(1, -1) : value.trimEnd();
    // A separator the greedy multiword value ate is put back, so the
    // surrounding line still reads as a delimited list.
    const trailingComma = !quote && raw.endsWith(',') ? ',' : '';
    const bare = trailingComma ? raw.slice(0, -1).trimEnd() : raw;
    const masked = maskKnownSensitiveValue(key, bare);
    return `${key}${sep === ':' ? ': ' : '='}${quote}${masked}${quote}${trailingComma}`;
  };

  out = out.replace(LABELED_FREEFORM_KV_RE, replaceLabeled(true));
  out = out.replace(LABELED_GROUPED_NUMERIC_RE, (whole, key: string, sep: string, value: string) => {
    if (!isGroupedNumericKey(key)) return whole;
    return `${key}${sep === ':' ? ': ' : '='}${maskKnownSensitiveValue(key, value)}`;
  });
  out = out.replace(LABELED_KV_RE, replaceLabeled(false));

  out = out.replace(IBAN_RE, (m) =>
    isValidIban(m) ? m.slice(0, 2) + '*'.repeat(Math.max(0, m.length - 6)) + m.slice(-4) : m
  );
  out = out.replace(PAN_RE, (m) => {
    const digits = m.replace(/\D/g, '');
    return passesLuhn(digits) ? maskPan(digits) : m;
  });
  out = out.replace(E164_RE, (m) => '+' + maskMobile(m));

  return out;
}

// --- structured values -------------------------------------------------------

const MAX_DEPTH = 12;

/**
 * Deep-masks a parsed object (an event, its `attributes`, a query result row)
 * before it is rendered. A sensitive KEY masks its whole subtree -- a
 * `customer` object is sensitive wholesale, not field by field -- and every
 * other string is still run through maskText, because a non-sensitive key
 * like `message` routinely carries a PAN in its text.
 */
function maskSensitiveSubtree(key: string, value: unknown): unknown {
  if (value === null || value === undefined) return value;
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return maskKnownSensitiveValue(key, String(value));
  }
  if (Array.isArray(value)) return value.map((v) => maskSensitiveSubtree(key, v));
  return MASKED;
}

export function maskDeep<T>(value: T, depth = 0): T {
  if (depth > MAX_DEPTH) return value;
  if (typeof value === 'string') return maskText(value) as unknown as T;
  if (Array.isArray(value)) return value.map((v) => maskDeep(v, depth + 1)) as unknown as T;
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      if (isSensitiveKey(k)) {
        // Scalars mask in place; arrays keep their shape with every scalar
        // leaf masked (parser_ILA_Bank emits key_values as {key: [values]},
        // and collapsing that to a bare string reads as data loss rather
        // than masking). A nested OBJECT under a sensitive key is masked
        // wholesale -- a `customer` object is sensitive as a unit.
        out[k] = maskSensitiveSubtree(k, v);
      } else {
        out[k] = maskDeep(v, depth + 1);
      }
    }
    return out as unknown as T;
  }
  return value;
}

/** Convenience for rendering an already-stringified blob. */
export function maskJsonString(value: unknown, space = 2): string {
  return JSON.stringify(maskDeep(value), null, space);
}
