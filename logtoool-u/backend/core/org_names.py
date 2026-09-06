"""
Static Org_Number -> Bank_Name lookup, for labeling the "By Org" breakdown
in the OTP Processor view. The raw `<Org>` tag in OTP processor logs is a
short issuer/product code (e.g. "040", "AFS1", "12B") that's meaningless on
its own -- this table resolves it to a human-readable bank/product name so
the dashboard doesn't just show a wall of codes.

An org code is not always 1:1 with a bank -- several codes can belong to
the same bank (different products/currencies, e.g. "040"/"190"/"077" are
all AFS) -- so codes are kept distinct rather than merged, with the bank
name attached per-code.

A code missing from this table still gets counted, just displayed as the
raw code (see label_for_org's fallback).
"""
from typing import Dict, Optional, TypedDict


class OrgInfo(TypedDict):
    bank_name: str
    country_code: str
    country_name: str


ORG_NAMES: Dict[str, OrgInfo] = {
    "200": {"bank_name": "ABC Algeria", "country_code": "FR", "country_name": "France"},
    "110": {"bank_name": "ABC Egypt", "country_code": "EG", "country_name": "Egypt"},
    "039": {"bank_name": "ABC Jordan", "country_code": "JO", "country_name": "Jordan"},
    "112S": {"bank_name": "ABO Soft POS", "country_code": "OM", "country_name": "Oman"},
    "040": {"bank_name": "AFS", "country_code": "BH", "country_name": "Bahrain"},
    "190": {"bank_name": "AFS", "country_code": "BH", "country_name": "Bahrain"},
    "AFS1": {"bank_name": "AFS 3DSecure Vision Flex", "country_code": "BH", "country_name": "Bahrain"},
    "077E": {"bank_name": "AFS eChannel Services", "country_code": "BH", "country_name": "Bahrain"},
    "276": {"bank_name": "AFS Prepaid", "country_code": "BH", "country_name": "Bahrain"},
    "999": {"bank_name": "AFS SMS monitoring alert message service", "country_code": "BH", "country_name": "Bahrain"},
    "077": {"bank_name": "AFS-USD", "country_code": "BH", "country_name": "Bahrain"},
    "12B": {"bank_name": "Ahli Bank - Corporate products", "country_code": "OM", "country_name": "Oman"},
    "112": {"bank_name": "Ahli Bank - Oman", "country_code": "OM", "country_name": "Oman"},
    "12A": {"bank_name": "Ahli Bank - Oman Islamic", "country_code": "OM", "country_name": "Oman"},
    "034": {"bank_name": "Ahli United Bank", "country_code": "BH", "country_name": "Bahrain"},
    "035": {"bank_name": "Ahli United Bank", "country_code": "BH", "country_name": "Bahrain"},
    "A066": {"bank_name": "Al Salam Bank Debit", "country_code": "BH", "country_name": "Bahrain"},
    "066": {"bank_name": "Alsalam", "country_code": "BH", "country_name": "Bahrain"},
    "168": {"bank_name": "Al-Salam VISA EUR", "country_code": "BH", "country_name": "Bahrain"},
    "169": {"bank_name": "Al-Salam VISA GBP", "country_code": "BH", "country_name": "Bahrain"},
    "167": {"bank_name": "Al-Salam VISA USD", "country_code": "BH", "country_name": "Bahrain"},
    "094": {"bank_name": "ARAB BANK BAHRAIN", "country_code": "BH", "country_name": "Bahrain"},
    "095": {"bank_name": "ARAB BANK Egypt", "country_code": "EG", "country_name": "Egypt"},
    "091": {"bank_name": "Arab Bank Jordan", "country_code": "JO", "country_name": "Jordan"},
    "097": {"bank_name": "Arab Bank Palestine", "country_code": "PS", "country_name": "Palestine"},
    "093": {"bank_name": "ARAB BANK QATAR", "country_code": "QA", "country_name": "Qatar"},
    "092": {"bank_name": "ARAB BANK UAE", "country_code": "AE", "country_name": "United Arab Emirates"},
    "165": {"bank_name": "ASBB - Infinite Cards", "country_code": "BH", "country_name": "Bahrain"},
    "164": {"bank_name": "ASBB - Visa Signature Card Contactless", "country_code": "BH", "country_name": "Bahrain"},
    "166": {"bank_name": "ASBB PREPAID CARD – AED", "country_code": "BH", "country_name": "Bahrain"},
    "163": {"bank_name": "ASBB Turkish Lira", "country_code": "BH", "country_name": "Bahrain"},
    "161": {"bank_name": "ASBBEUR", "country_code": "BH", "country_name": "Bahrain"},
    "162": {"bank_name": "ASBBGBP", "country_code": "BH", "country_name": "Bahrain"},
    "160": {"bank_name": "ASBBUSD", "country_code": "BH", "country_name": "Bahrain"},
    "198": {"bank_name": "ASBS", "country_code": "ZZ", "country_name": "Unknown or Invalid Region"},
    "046": {"bank_name": "Bank Dhofar", "country_code": "OM", "country_name": "Oman"},
    "016": {"bank_name": "Bank Dhofar - Master", "country_code": "OM", "country_name": "Oman"},
    "123": {"bank_name": "Bank Dhofar - MC Corporate, MC Business", "country_code": "OM", "country_name": "Oman"},
    "017": {"bank_name": "Bank Dhofar - MC Gold, MC Titanium", "country_code": "OM", "country_name": "Oman"},
    "037": {"bank_name": "BMI", "country_code": "BH", "country_name": "Bahrain"},
    "19B": {"bank_name": "Bpay", "country_code": "BH", "country_name": "Bahrain"},
    "19C": {"bank_name": "Bpay Vcard", "country_code": "BH", "country_name": "Bahrain"},
    "024": {"bank_name": "BSB", "country_code": "BH", "country_name": "Bahrain"},
    "061": {"bank_name": "Commercial Bank Int'l", "country_code": "AE", "country_name": "United Arab Emirates"},
    "111": {"bank_name": "Commercial Bank of Kuwait", "country_code": "KW", "country_name": "Kuwait"},
    "FGLBBANK": {"bank_name": "First Gulf Libyan Bank", "country_code": "LY", "country_name": "Libya"},
    "241": {"bank_name": "GIB Bahrain Corporate", "country_code": "BH", "country_name": "Bahrain"},
    "240": {"bank_name": "GIB KSA Corporate", "country_code": "SA", "country_name": "Saudi Arabia"},
    "242": {"bank_name": "GIB UAE Corporate", "country_code": "AE", "country_name": "United Arab Emirates"},
    "150": {"bank_name": "Gulf African Bank", "country_code": "KE", "country_name": "Kenya"},
    "GAB": {"bank_name": "Gulf African Bank", "country_code": "KE", "country_name": "Kenya"},
    "A171": {"bank_name": "Gulf African Bank Debit", "country_code": "KE", "country_name": "Kenya"},
    "089": {"bank_name": "IIAB", "country_code": "JO", "country_name": "Jordan"},
    "ILAI": {"bank_name": "ila alburaq Islamic", "country_code": "BH", "country_name": "Bahrain"},
    "ILA": {"bank_name": "ILA Bank", "country_code": "BH", "country_name": "Bahrain"},
    "ILAJ": {"bank_name": "ILA Jordon", "country_code": "JO", "country_name": "Jordan"},
    "084": {"bank_name": "KFH", "country_code": "BH", "country_name": "Bahrain"},
    "004": {"bank_name": "Mawarid Finance", "country_code": "AE", "country_name": "United Arab Emirates"},
    "MEDITLBY": {"bank_name": "Mediterranean Bank", "country_code": "LY", "country_name": "Libya"},
    "225": {"bank_name": "National Bank of Fujairah", "country_code": "AE", "country_name": "United Arab Emirates"},
    "135": {"bank_name": "Nizwa Bank", "country_code": "OM", "country_name": "Oman"},
    "051": {"bank_name": "Oman Arab Bank", "country_code": "OM", "country_name": "Oman"},
    "062": {"bank_name": "SIB", "country_code": "AE", "country_name": "United Arab Emirates"},
    "058S": {"bank_name": "SOHAR SOFT POS", "country_code": "OM", "country_name": "Oman"},
    "021": {"bank_name": "United Bank Limited", "country_code": "QA", "country_name": "Qatar"},
}


def get_org_info(code: Optional[str]) -> Optional[OrgInfo]:
    if not code:
        return None
    return ORG_NAMES.get(code) or ORG_NAMES.get(code.upper())


def label_for_org(code: Optional[str]) -> str:
    """Human-readable "By Org" chart label: "Bank Name (code)", or the raw
    code (or "UNKNOWN") when the code isn't in ORG_NAMES."""
    if not code:
        return "UNKNOWN"
    info = get_org_info(code)
    return f"{info['bank_name']} ({code})" if info else code
