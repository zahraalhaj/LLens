/**
 * Regression corpus for src/utils/maskSensitive.ts.
 *
 * HOW TO USE THIS FILE
 * --------------------
 * Sensitive-data detection is tuned reactively: real logs keep exposing
 * formats no pattern anticipated. Every time one does, add a case here
 * FIRST (it will fail), then fix the detector until it passes. Nothing gets
 * fixed twice, and no fix silently breaks an earlier one.
 *
 *     npm run check:masking
 *
 * `mustNotSurvive` is a leak check -- these substrings must be gone from the
 * masked output. `mustSurvive` is the equally important half: operational
 * data the tool is useless without. Over-masking a tracker id or a merchant
 * name is a real defect, not a safe default, so those cases carry the same
 * weight as the leaks.
 *
 * `note` records WHERE the format came from. Provenance is what keeps this
 * corpus honest -- a case traceable to a real parser or log file is evidence;
 * an invented one is a guess.
 */

export interface MaskCase {
  name: string;
  line: string;
  /** Substrings that must NOT appear in the masked output. */
  mustNotSurvive?: string[];
  /** Substrings that MUST still appear -- guards against over-masking. */
  mustSurvive?: string[];
  /** Where this format was observed. */
  note: string;
}

export const MASK_CASES: MaskCase[] = [
  // ---------------------------------------------------------------- OTP
  {
    name: 'otp-prose-vflex',
    line: 'Dear Customer, 482913 is your OTP for card XXXXXXXXXXXX1234 at DEMO COFFEE for USD 150.00.',
    mustNotSurvive: ['482913'],
    mustSurvive: ['DEMO COFFEE', '150.00'],
    note: 'Dispatched SMS body. Exact shape of parser_VFlex.py OTP_MESSAGE_RE -- OTP precedes its label.',
  },
  {
    name: 'otp-prose-verification',
    line: 'SMS body: 774812 is your verification code. Do not share it.',
    mustNotSurvive: ['774812'],
    note: 'Same digits-before-label shape with a different label word.',
  },
  {
    name: 'otp-prose-label-first',
    line: 'Sent SMS: Your one-time password is 903221 valid for 5 minutes',
    mustNotSurvive: ['903221'],
    note: 'Label-then-digits; guards the original OTP_RE path.',
  },
  {
    name: 'otp-xml-tag',
    line: '<Body><OTP>482913</OTP><OTPPAN>0002</OTPPAN></Body>',
    mustNotSurvive: ['482913'],
    mustSurvive: ['0002'],
    note: 'demo_logs/otp_online_processor_DEMO.log. OTPPAN is already a derived last-4 and must stay.',
  },

  // --------------------------------------------------------------- cards
  {
    name: 'pan-iso8583-f2',
    line: '<Iso8583PostXml><MsgType>0200</MsgType><Fields><F2>4000000000000002</F2></Fields></Iso8583PostXml>',
    mustNotSurvive: ['4000000000000002'],
    mustSurvive: ['0200'],
    note: 'demo_logs/abce_credit_portal_DEMO.log. ISO8583 field 2 is the PAN; the key name gives no hint.',
  },
  { name: 'pan-spaced', line: 'card 4111 1111 1111 1111 declined', mustNotSurvive: ['4111 1111 1111 1111'], note: 'Card written in groups of four.' },
  { name: 'pan-dashed', line: 'card 4111-1111-1111-1111 declined', mustNotSurvive: ['4111-1111-1111-1111'], note: 'Dash-separated variant.' },
  { name: 'pan-amex15', line: 'amex 378282246310005 approved', mustNotSurvive: ['378282246310005'], note: '15-digit Amex; length range must not assume 16.' },
  { name: 'pan-13digit', line: 'visa13 4222222222222 ok', mustNotSurvive: ['4222222222222'], note: '13-digit legacy Visa, the lower bound of the range.' },
  {
    name: 'pan-keeps-trailing-space',
    line: 'card 4000000000000002 declined',
    mustSurvive: [' declined'],
    note: 'Regression: a trailing separator inside the match used to be eaten, joining the next word.',
  },
  {
    name: 'card-already-masked-star',
    line: '<MaskedCardNo>400000******0002</MaskedCardNo>',
    mustSurvive: ['400000******0002'],
    note: 'Source system already masked it; re-masking destroys the last-4 and protects nothing.',
  },
  {
    name: 'card-already-masked-x',
    line: 'card XXXXXXXXXXXX1234 blocked',
    mustSurvive: ['XXXXXXXXXXXX1234'],
    note: 'parser_VFlex.py MASKED_CARD_RE form.',
  },

  // -------------------------------------------------------------- mobile
  { name: 'mobile-e164-xml', line: '<Mobile>+97333445566</Mobile>', mustNotSurvive: ['97333445566'], note: 'demo_logs OTP processor header.' },
  { name: 'mobile-bare-intl', line: 'Mobile=0097333445566 notified', mustNotSurvive: ['97333445566'], note: '00-prefixed international form.' },
  {
    name: 'mobile-spaced-groups',
    line: 'Mobile: +973 3344 5566',
    mustNotSurvive: ['3344 5566'],
    note: 'Space-grouped number. The tight value pattern stopped at the first space and leaked the rest.',
  },

  // --------------------------------------------------------------- email
  { name: 'email-plain', line: 'notify jordan.t@ilabank.com', mustNotSurvive: ['jordan.t@ilabank.com'], mustSurvive: ['ilabank.com'], note: 'Domain is kept deliberately -- it is useful in aggregate and is not itself identifying.' },
  { name: 'email-subaddress', line: 'to j.test+alerts@ila-bank.co.uk queued', mustNotSurvive: ['j.test+alerts@'], note: 'Plus-addressing and a multi-part TLD.' },
  { name: 'email-xml-tag', line: '<EMAIL>morgan.example@bank.bh</EMAIL>', mustNotSurvive: ['morgan.example@'], note: 'Email inside an XML payload tag.' },

  // -------------------------------------------------------------- secrets
  {
    name: 'secret-bearer-header',
    line: 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc123def456',
    mustNotSurvive: ['eyJhbGciOiJIUzI1NiJ9'],
    mustSurvive: ['Bearer'],
    note: 'The value capture used to stop on the scheme word, redacting "Bearer" and leaving the token.',
  },
  { name: 'secret-bare-jwt', line: 'decoded token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NSJ9.abcdef', mustNotSurvive: ['eyJzdWIiOiIxMjM0NSJ9'], note: 'JWT with no label; the eyJ prefix is specific enough to act on alone.' },
  { name: 'secret-password', line: 'auth failed, password: hunter2secret', mustNotSurvive: ['hunter2secret'], note: 'Labelled password.' },
  { name: 'secret-apikey', line: 'apikey=AKIA1234567890ABC', mustNotSurvive: ['AKIA1234567890ABC'], note: 'Labelled API key.' },
  { name: 'secret-json-key', line: '{"api_key": "sk-live-9f8a7b6c5d4e3f2a1b"}', mustNotSurvive: ['sk-live-9f8a7b6c5d4e3f2a1b'], note: 'Secret inside an embedded JSON payload.' },
  { name: 'secret-cvv', line: 'cvv=123 exp=1228', mustNotSurvive: ['cvv=123'], note: 'A 4-or-fewer-digit secret has no safe partial to keep.' },

  // ---------------------------------------------------- identifiers / PII
  { name: 'pii-cif-account', line: 'CC_AccountEnquiry(OrgNo=900, CIF=50021, AccountNo=000000123, Channel=DEMO_WEB)', mustNotSurvive: ['CIF=50021', 'AccountNo=000000123'], mustSurvive: ['OrgNo=900', 'DEMO_WEB'], note: 'demo_logs/abce_credit_portal_DEMO.log. OrgNo and Channel are operational and must stay.' },
  { name: 'pii-multiword-name', line: 'CustomerName=Jordan Testperson, Email=j.t@ilabank.com', mustNotSurvive: ['Testperson'], note: 'Stopping the value at the first space masked "Jordan" and left the surname.' },
  { name: 'pii-address', line: 'Address=Building 12, Road 2830, Manama, Channel=WEB', mustNotSurvive: ['Road 2830'], mustSurvive: ['Channel=WEB'], note: 'Address spans commas; must not swallow the following key.' },
  { name: 'iban-real', line: 'credited to GB29NWBK60161331926819 today', mustNotSurvive: ['GB29NWBK60161331926819'], note: 'Valid mod-97 IBAN.' },

  // ------------------------------------ MUST SURVIVE: operational data
  { name: 'op-log-tracker', line: 'Log Tracker No: TRK-DEMO-9001 => Credit posting accepted', mustSurvive: ['TRK-DEMO-9001'], note: 'Primary correlation key across every parser in this repo.' },
  {
    name: 'op-vflex-tracker',
    line: 'TrackingID SU100000000000000001 CardNo',
    mustSurvive: ['SU100000000000000001'],
    note: 'parser_VFlex.py TRACKER_RE. Two letters + 18 digits is IBAN-shaped and was being destroyed.',
  },
  { name: 'op-iso-amount-fields', line: '<F4>000000015000</F4><F7>0827090245</F7><F37>DEMOREF000002</F37>', mustSurvive: ['000000015000', '0827090245', 'DEMOREF000002'], note: 'ISO8583 amount, transmission time and retrieval reference.' },
  { name: 'op-datetime-digits', line: 'TranDate=20260827 TranTime=150010 concatenated=20260827150010', mustSurvive: ['20260827', '150010', '20260827150010'], note: 'A 14-digit datetime is PAN-shaped; Luhn is what separates them.' },
  { name: 'op-long-sequence', line: 'seq=1234567890123456789 ref=20260827091003', mustSurvive: ['1234567890123456789'], note: '19-digit non-Luhn sequence number.' },
  { name: 'op-merchant', line: '<MerchantName>DEMO COFFEE HOUSE</MerchantName><MerchantId>DEMOMERCH01</MerchantId>', mustSurvive: ['DEMO COFFEE HOUSE', 'DEMOMERCH01'], note: 'Business data, not PII. Drives InvestigationView merchant filtering.' },
  { name: 'op-host-port', line: 'host=10.20.30.40:8443 filename=ila.log servicename=CoreAdapter', mustSurvive: ['10.20.30.40:8443', 'ila.log', 'CoreAdapter'], note: 'Keys containing sensitive-looking words that are operational.' },
  { name: 'op-queue-msgid', line: 'Message for Queue=mq-demo-otp-in-push, MsgId: DEMOMSG0001', mustSurvive: ['mq-demo-otp-in-push', 'DEMOMSG0001'], note: 'Queue names contain "otp" but carry no OTP value.' },
  { name: 'op-stack-frame', line: 'at Afs.Core.Adapter.PostAsync(String url) in C:\\src\\Adapter.cs:line 142', mustSurvive: ['Adapter.cs', '142'], note: 'Stack frames from parser_ILA_Bank multiline entries.' },
  { name: 'op-duration', line: 'End of request, duration 00:00:01.549', mustSurvive: ['00:00:01.549'], note: 'Explicit duration; colons must not read as key/value.' },
  { name: 'op-currency-amount', line: '<TranCurrency>840</TranCurrency><TranAmount>150.00</TranAmount>', mustSurvive: ['840', '150.00'], note: 'Numeric currency code and amount.' },
  { name: 'op-uuid', line: 'correlation 3f2504e0-4f89-41d3-9a0c-0305e82c3301 traced', mustSurvive: ['3f2504e0'], note: 'UUID correlation id extracted by parser_ILA_Bank.' },
  { name: 'op-prose-after-value', line: 'msg=hello world here CIF=50021 retry scheduled', mustSurvive: ['retry scheduled'], note: 'A masked value must not swallow the prose that follows it.' },
];
