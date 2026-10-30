"""Shared plumbing for the COSMOS pages: one retrying HTTP session, JSON fetching with friendly errors,
URL/markdown sanitising, and a latitude/longitude box that remembers your location across pages."""
from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TIMEOUT = 15
USER_AGENT = "COSMOS/1.0 (+https://github.com/manavmistry30/COSMOS)"
LOCATION_KEY = "_my_location"      # last valid "lat, lon" typed on any page


MAX_RETRY_AFTER_S = 10              # wait out short rate limits; give up on long ones instead of freezing the page


class ApiError(Exception):
    pass


# ───────────────────────────── HTTP ─────────────────────────────

class CappedRetry(Retry):
    """Honours short Retry-After headers, but ignores long ones (NASA's DEMO_KEY asks for ~11 h) and falls back
    to the normal short backoff, so a rate-limited request fails in seconds instead of freezing the page."""

    def get_retry_after(self, response) -> float | None:
        after = super().get_retry_after(response)
        return after if after is not None and after <= MAX_RETRY_AFTER_S else None


@st.cache_resource
def _session() -> requests.Session:
    s = requests.Session()
    retry = CappedRetry(
        total=3,
        connect=3,
        read=2,
        status=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=16))
    return s


def get_json(url: str, params: dict | None = None, service: str = "The data service", timeout: float = TIMEOUT) -> Any:
    """GET a JSON document, turning every failure into an ApiError with a readable message."""
    try:
        r = _session().get(url, params=params, timeout=(3.05, timeout),
                           headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        r.raise_for_status()
        return r.json()
    except requests.Timeout as e:
        raise ApiError(f"{service} timed out. Try again shortly.") from e
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        if status == 429:
            raise ApiError(f"{service} is rate-limiting requests. Try again in a little while.") from e
        raise ApiError(f"{service} returned HTTP {status}. Try again shortly.") from e
    except requests.exceptions.RetryError as e:     # retries exhausted on 429/5xx
        raise ApiError(f"{service} is busy or rate-limiting requests. Try again in a little while.") from e
    except ValueError as e:
        raise ApiError(f"{service} returned invalid JSON.") from e
    except requests.RequestException as e:
        raise ApiError(f"Could not connect to {service[0].lower() + service[1:]}. Check the connection and try again.") from e


# ───────────────────────────── Sanitising ─────────────────────────────

def safe_url(u: Any) -> str:
    """Return a well-formed HTTPS URL (legacy HTTP links are upgraded), or '' if it isn't safe to show."""
    u = str(u or "").strip()
    try:
        parts = urlsplit(u)
        if parts.scheme.lower() == "http":
            parts = parts._replace(scheme="https")
        if (parts.scheme.lower() != "https" or not parts.hostname
                or parts.username or parts.password or parts.port not in (None, 443)):
            return ""
        return urlunsplit((parts.scheme.lower(), parts.netloc,
                           quote(parts.path, safe="/%:@!$&'()*+,;=-._~%"),
                           quote(parts.query, safe="/?@!$&'()*+,;=:-._~%"),
                           quote(parts.fragment, safe="/?@!$&'()*+,;=:-._~%")))
    except ValueError:
        return ""


def md_escape(text: str) -> str:
    """Keep API text and user input literal when inserted into Markdown."""
    return re.sub(r"([\\`*_{}\[\]()<>#+\-.!|~$])", r"\\\1", str(text).replace("\r\n", "\n"))


# ───────────────────────────── Location ─────────────────────────────

def valid_latlon(lat: float, lon: float) -> bool:
    return (math.isfinite(lat) and math.isfinite(lon)
            and -90 <= lat <= 90 and -180 <= lon <= 180)


def parse_latlon(text: str) -> tuple[float, float] | None:
    try:
        if not isinstance(text, str):
            return None
        parts = re.split(r"[,;]", text)
        if len(parts) != 2:
            return None
        lat, lon = (float(x.strip()) for x in parts)
    except (TypeError, ValueError):
        return None
    return (lat, lon) if valid_latlon(lat, lon) else None


def fmt_latlon(lat: float, lon: float) -> str:
    """12.3°N, 45.6°W style."""
    return f"{abs(lat):.1f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.1f}°{'E' if lon >= 0 else 'W'}"


def location_input(key: str, placeholder: str = "e.g. 51.5, -0.12") -> tuple[float, float] | None:
    """'Your latitude, longitude' box. A valid entry is remembered and pre-filled on the other pages."""
    if key not in st.session_state and st.session_state.get(LOCATION_KEY):
        st.session_state[key] = st.session_state[LOCATION_KEY]
    text = st.text_input("Your latitude, longitude", key=key, placeholder=placeholder,
                         help="Decimal degrees: north and east are positive. Remembered while you switch pages.")
    if not text:
        return None
    here = parse_latlon(text)
    if here is None:
        st.warning("Enter two numbers like `51.5, -0.12` (latitude -90 to 90, longitude -180 to 180).")
        return None
    st.session_state[LOCATION_KEY] = text
    return here
