"""
Static ISO 4217 currency -> representative country centroid lookup, for
plotting a currency's transaction volume on a world map/globe.

A currency doesn't map 1:1 to a single country (EUR alone is legal tender in
~20), so each entry picks one representative issuing/anchor country -- this
is a display aid for "where in the world is this currency showing up", not
a claim of exclusivity. Coordinates are approximate country centroids,
precise enough for a globe pin at world zoom.

Covers the currencies actually observed in this tool's log formats (AED,
SAR, QAR, OMR, BHD, JOD, EGP -- Gulf/Middle East payment processors -- plus
USD/EUR/GBP/INR/CNY/VND/THB/TRY/AUD) and a broader set of common world
currencies so new sources aren't left unmapped by default. A currency
missing from this table is still counted server-side, just excluded from
the map's plotted points (surfaced separately as "unmapped").
"""
from typing import Dict, TypedDict


class CurrencyGeo(TypedDict):
    country: str
    lat: float
    lng: float


CURRENCY_GEO: Dict[str, CurrencyGeo] = {
    "AED": {"country": "United Arab Emirates", "lat": 23.4241, "lng": 53.8478},
    "SAR": {"country": "Saudi Arabia", "lat": 23.8859, "lng": 45.0792},
    "QAR": {"country": "Qatar", "lat": 25.3548, "lng": 51.1839},
    "OMR": {"country": "Oman", "lat": 21.4735, "lng": 55.9754},
    "BHD": {"country": "Bahrain", "lat": 25.9304, "lng": 50.6378},
    "KWD": {"country": "Kuwait", "lat": 29.3117, "lng": 47.4818},
    "JOD": {"country": "Jordan", "lat": 30.5852, "lng": 36.2384},
    "EGP": {"country": "Egypt", "lat": 26.8206, "lng": 30.8025},
    "LBP": {"country": "Lebanon", "lat": 33.8547, "lng": 35.8623},
    "ILS": {"country": "Israel", "lat": 31.0461, "lng": 34.8516},
    "IQD": {"country": "Iraq", "lat": 33.2232, "lng": 43.6793},
    "TRY": {"country": "Turkey", "lat": 38.9637, "lng": 35.2433},
    "USD": {"country": "United States", "lat": 37.0902, "lng": -95.7129},
    "CAD": {"country": "Canada", "lat": 56.1304, "lng": -106.3468},
    "MXN": {"country": "Mexico", "lat": 23.6345, "lng": -102.5528},
    "BRL": {"country": "Brazil", "lat": -14.2350, "lng": -51.9253},
    "ARS": {"country": "Argentina", "lat": -38.4161, "lng": -63.6167},
    "EUR": {"country": "Eurozone (Germany)", "lat": 51.1657, "lng": 10.4515},
    "GBP": {"country": "United Kingdom", "lat": 55.3781, "lng": -3.4360},
    "CHF": {"country": "Switzerland", "lat": 46.8182, "lng": 8.2275},
    "SEK": {"country": "Sweden", "lat": 60.1282, "lng": 18.6435},
    "NOK": {"country": "Norway", "lat": 60.4720, "lng": 8.4689},
    "DKK": {"country": "Denmark", "lat": 56.2639, "lng": 9.5018},
    "PLN": {"country": "Poland", "lat": 51.9194, "lng": 19.1451},
    "RUB": {"country": "Russia", "lat": 61.5240, "lng": 105.3188},
    "INR": {"country": "India", "lat": 20.5937, "lng": 78.9629},
    "CNY": {"country": "China", "lat": 35.8617, "lng": 104.1954},
    "JPY": {"country": "Japan", "lat": 36.2048, "lng": 138.2529},
    "KRW": {"country": "South Korea", "lat": 35.9078, "lng": 127.7669},
    "HKD": {"country": "Hong Kong", "lat": 22.3193, "lng": 114.1694},
    "SGD": {"country": "Singapore", "lat": 1.3521, "lng": 103.8198},
    "MYR": {"country": "Malaysia", "lat": 4.2105, "lng": 101.9758},
    "THB": {"country": "Thailand", "lat": 15.8700, "lng": 100.9925},
    "VND": {"country": "Vietnam", "lat": 14.0583, "lng": 108.2772},
    "IDR": {"country": "Indonesia", "lat": -0.7893, "lng": 113.9213},
    "PHP": {"country": "Philippines", "lat": 12.8797, "lng": 121.7740},
    "PKR": {"country": "Pakistan", "lat": 30.3753, "lng": 69.3451},
    "BDT": {"country": "Bangladesh", "lat": 23.6850, "lng": 90.3563},
    "LKR": {"country": "Sri Lanka", "lat": 7.8731, "lng": 80.7718},
    "AUD": {"country": "Australia", "lat": -25.2744, "lng": 133.7751},
    "NZD": {"country": "New Zealand", "lat": -40.9006, "lng": 174.8860},
    "ZAR": {"country": "South Africa", "lat": -30.5595, "lng": 22.9375},
    "NGN": {"country": "Nigeria", "lat": 9.0820, "lng": 8.6753},
    "KES": {"country": "Kenya", "lat": -0.0236, "lng": 37.9062},
    "MAD": {"country": "Morocco", "lat": 31.7917, "lng": -7.0926},
}
