"""Live Space Tracker: crew in orbit, live ISS position, orbit track, and a 'how far is it from me' check."""
from __future__ import annotations

import html
import math
import re
import time
from collections import Counter
from datetime import date
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import pydeck as pdk
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from theme import eyebrow, facts_html

TIMEOUT = 10
LL2_URL = "https://ll.thespacedevs.com/2.3.0/astronauts/"
ISS_URL = "https://api.wheretheiss.at/v1/satellites/25544"
ISS_POSITIONS_URL = ISS_URL + "/positions"

EARTH_R_KM = 6371.0
MU_EARTH = 398600.4418   # km^3/s^2


class ApiError(Exception):
    pass


# ───────────────────────────── Pure helpers (no I/O) ─────────────────────────────

def orbital_period_min(altitude_km: float) -> float:
    """Period of a circular orbit at the given altitude."""
    if not math.isfinite(altitude_km) or altitude_km <= 0:
        raise ValueError("Altitude must be a positive, finite number of kilometres.")
    a = EARTH_R_KM + altitude_km
    return 2 * math.pi * math.sqrt(a**3 / MU_EARTH) / 60


def _valid_latlon(lat: float, lon: float) -> bool:
    return (math.isfinite(lat) and math.isfinite(lon)
            and -90 <= lat <= 90 and -180 <= lon <= 180)


def look_angles(obs_lat: float, obs_lon: float, sat_lat: float, sat_lon: float, alt_km: float):
    """Return (ground distance km, straight-line distance km, elevation above horizon in degrees)."""
    if not _valid_latlon(obs_lat, obs_lon) or not _valid_latlon(sat_lat, sat_lon):
        raise ValueError("Latitude or longitude is outside its valid range.")
    if not math.isfinite(alt_km) or alt_km <= 0:
        raise ValueError("Altitude must be a positive, finite number of kilometres.")
    p1, p2 = math.radians(obs_lat), math.radians(sat_lat)
    dlat, dlon = p2 - p1, math.radians(sat_lon - obs_lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2   # haversine: stable for small angles
    c = 2 * math.asin(min(1.0, math.sqrt(h)))
    cos_c = math.cos(c)
    r = EARTH_R_KM + alt_km
    ground = EARTH_R_KM * c
    slant = math.sqrt(max(0.0, r**2 + EARTH_R_KM**2 - 2 * r * EARTH_R_KM * cos_c))
    elevation = math.degrees(math.atan2(r * cos_c - EARTH_R_KM, r * math.sin(c)))
    return ground, slant, elevation


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
    return (lat, lon) if _valid_latlon(lat, lon) else None


def split_at_antimeridian(points: list[tuple[float, float]]) -> list[list[list[float]]]:
    """[(lon, lat), ...] -> path segments, split wherever the track wraps across +/-180 degrees."""
    segments: list[list[list[float]]] = []
    current: list[list[float]] = []
    prev_lon = None
    for lon, lat in points:
        if prev_lon is not None and abs(lon - prev_lon) > 180:
            segments.append(current)
            current = []
        current.append([lon, lat])
        prev_lon = lon
    if current:
        segments.append(current)
    return [s for s in segments if len(s) > 1]


DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


def parse_duration_seconds(s: Any) -> float | None:
    """ISO 8601 duration such as 'P396DT11H33M45S' or 'PT8H10M' -> seconds (None if unreadable)."""
    m = DURATION_RE.match(str(s or "").strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi, sec = (float(g) if g else 0.0 for g in m.groups())
    return d * 86_400 + h * 3_600 + mi * 60 + sec


def fmt_hours(seconds: float | None) -> str:
    if seconds is None:
        return ""
    h, rem = divmod(int(seconds), 3600)
    return f"{h} h {rem // 60} min" if h else f"{rem // 60} min"


def fmt_span(seconds: float | None) -> str:
    if seconds is None:
        return ""
    days = seconds / 86_400
    if days >= 365:
        return f"{days:,.0f} days ({days / 365.25:.1f} years)"
    return f"{days:,.0f} days" if days >= 1 else fmt_hours(seconds)


def age_from_dob(dob: str, today: date | None = None) -> int | None:
    try:
        born = date.fromisoformat(dob[:10])
    except (TypeError, ValueError):
        return None
    t = today or date.today()
    if born > t:
        return None
    return t.year - born.year - ((t.month, t.day) < (born.month, born.day))


def days_since(iso: str, today: date | None = None) -> int | None:
    try:
        d = date.fromisoformat(iso[:10])
    except (TypeError, ValueError):
        return None
    n = ((today or date.today()) - d).days
    return n if n >= 0 else None


def safe_url(u: Any) -> str:
    """Return a well-formed HTTPS URL; upgrade legacy HTTP links when safe."""
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
    """Stop text from the API being interpreted as markdown."""
    return re.sub(r"([\\`*_{}\[\]()<>#+\-.!|~$])", r"\\\1", text.replace("\r\n", "\n"))


def parse_astronaut(a: dict) -> dict:
    """One Launch Library 2 astronaut record -> the fields the page shows."""
    if not isinstance(a, dict):
        a = {}
    agency = a.get("agency") or {}
    if not isinstance(agency, dict):
        agency = {}
    nat = a.get("nationality")
    if isinstance(nat, list):
        nat = ", ".join(str(n.get("name") or "") for n in nat if isinstance(n, dict))
    elif isinstance(nat, dict):
        nat = nat.get("name", "")
    status = a.get("status")
    if isinstance(status, dict):
        status = status.get("name", "")
    image = a.get("image")
    if isinstance(image, dict):
        image = image.get("thumbnail_url") or image.get("image_url")
    else:
        image = a.get("profile_image") or a.get("profile_image_thumbnail")
    return {
        "name": str(a.get("name") or "Unknown"),
        "agency": str(agency.get("name") or ""),
        "agency_abbrev": str(agency.get("abbrev") or ""),
        "group": str(agency.get("name") or nat or "Unknown"),
        "nationality": str(nat or ""),
        "status": str(status or ""),
        "dob": str(a.get("date_of_birth") or ""),
        "age": a.get("age") if isinstance(a.get("age"), int) else None,
        "bio": str(a.get("bio") or "").strip(),
        "image": safe_url(image),
        "wiki": safe_url(a.get("wiki")),
        "twitter": safe_url(a.get("twitter")),
        "instagram": safe_url(a.get("instagram")),
        "time_in_space_s": parse_duration_seconds(a.get("time_in_space")),
        "eva_s": parse_duration_seconds(a.get("eva_time")),
        "flights": a.get("flights_count") if isinstance(a.get("flights_count"), int) else None,
        "spacewalks": a.get("spacewalks_count") if isinstance(a.get("spacewalks_count"), int) else None,
        "last_flight": str(a.get("last_flight") or ""),
        "craft": "",
    }


def person_meta(p: dict, today: date | None = None) -> str:
    age = age_from_dob(p["dob"], today) if p["dob"] else None
    age = age if age is not None else p["age"]
    parts = [p["agency_abbrev"] or p["agency"], p["nationality"], f"age {age}" if age is not None else ""]
    return " · ".join(x for x in parts if x)


def person_facts(p: dict, today: date | None = None) -> list[tuple[str, str]]:
    facts: list[tuple[str, str]] = []
    ago = days_since(p["last_flight"], today)
    if ago is not None:
        launched = date.fromisoformat(p["last_flight"][:10])
        facts.append(("Latest launch", f"{launched:%d %b %Y} · {ago:,} days ago"))
    if p["time_in_space_s"] is not None:
        facts.append(("Time in space", fmt_span(p["time_in_space_s"])))
    walks = p["spacewalks"]
    if walks is not None:
        if not walks:
            facts.append(("Spacewalks", "none yet"))
        elif p["eva_s"] is not None:
            facts.append(("Spacewalks", f"{walks} · {fmt_hours(p['eva_s'])} outside"))
        else:
            facts.append(("Spacewalks", str(walks)))
    if p["flights"] is not None:
        facts.append(("Flights", str(p["flights"])))
    return facts


def initials(name: str) -> str:
    parts = [w for w in re.split(r"[\s.]+", name) if w]
    return "".join(w[0] for w in (parts[:1] + parts[-1:] if len(parts) > 1 else parts[:1])).upper() or "?"


def crew_summary(people: list[dict]) -> str:
    counts = Counter(p["agency_abbrev"] or p["agency"] or p["craft"] or p["group"] for p in people)
    return " · ".join(f"{k} {v}" for k, v in counts.most_common())


# ───────────────────────────── API clients ─────────────────────────────

@st.cache_resource
def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=2,
        status=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
    s.mount("https://", adapter)
    return s


def get_json(url: str, params: dict | None = None) -> Any:
    try:
        r = _session().get(
            url,
            params=params,
            timeout=(3.05, TIMEOUT),
            headers={"Accept": "application/json", "User-Agent": "SpaceTracker/1.0"},
        )
        r.raise_for_status()
        return r.json()
    except requests.Timeout as e:
        raise ApiError("The space-data service timed out. Try again shortly.") from e
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        if status == 429:
            message = "The space-data service is rate-limiting requests. Try again in a little while."
        else:
            message = f"The space-data service returned HTTP {status}. Try again shortly."
        raise ApiError(message) from e
    except ValueError as e:
        raise ApiError("The space-data service returned invalid JSON.") from e
    except requests.RequestException as e:
        raise ApiError("Could not connect to the space-data service. Check the connection and try again.") from e


@st.cache_data(ttl=3600)   # long cache: the free API is rate-limited
def get_astronauts() -> list[dict]:
    data = get_json(LL2_URL, {"in_space": "true", "limit": 100})
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ApiError("The astronaut service returned an unexpected response.")
    people = [parse_astronaut(a) for a in data["results"] if isinstance(a, dict)]
    if data["results"] and not people:
        raise ApiError("The astronaut service returned no readable crew records.")
    return people


@st.cache_data(ttl=5)
def get_iss_position() -> dict:
    d = get_json(ISS_URL)
    if not isinstance(d, dict):
        raise ApiError("The ISS position service returned an unexpected response.")
    try:
        pos = {
            "lat": float(d["latitude"]),
            "lon": float(d["longitude"]),
            "altitude_km": float(d["altitude"]),
            "velocity_kmh": float(d["velocity"]),
            "visibility": str(d.get("visibility") or "unknown"),
            "footprint_km": float(d.get("footprint", 0)),
            "timestamp": int(d["timestamp"]),
        }
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise ApiError("The ISS position service returned incomplete telemetry.") from e
    if (not _valid_latlon(pos["lat"], pos["lon"])
            or not math.isfinite(pos["altitude_km"]) or pos["altitude_km"] <= 0
            or not math.isfinite(pos["velocity_kmh"]) or pos["velocity_kmh"] <= 0
            or not math.isfinite(pos["footprint_km"]) or pos["footprint_km"] < 0
            or pos["timestamp"] <= 0):
        raise ApiError("The ISS position service returned out-of-range telemetry.")
    return pos


@st.cache_data(ttl=1800, show_spinner=False)
def get_ground_track(anchor: int) -> list[dict]:
    """ISS positions every 3 minutes from 45 min before to 45 min after `anchor` (about one full orbit)."""
    stamps = list(range(anchor - 2700, anchor + 2701, 180))
    out: list[dict] = []
    for i in range(0, len(stamps), 10):            # API allows 10 timestamps per request
        chunk = stamps[i:i + 10]
        data = get_json(ISS_POSITIONS_URL, {"timestamps": ",".join(map(str, chunk)), "units": "kilometers"})
        if not isinstance(data, list):
            raise ApiError("The ISS track service returned an unexpected response.")
        try:
            for point in data:
                lat, lon = float(point["latitude"]), float(point["longitude"])
                stamp = int(point["timestamp"])
                if not _valid_latlon(lat, lon) or stamp <= 0:
                    continue
                out.append({"lat": lat, "lon": lon, "timestamp": stamp})
        except (KeyError, TypeError, ValueError) as e:
            raise ApiError("The ISS track service returned incomplete telemetry.") from e
        if i + 10 < len(stamps):
            time.sleep(1.0)                        # free API allows about 1 request per second
    out.sort(key=lambda p: p["timestamp"])
    if len(out) < 2:
        raise ApiError("The ISS track service returned too few valid positions.")
    return out


# ───────────────────────────── Map ─────────────────────────────

def build_map(pos: dict, track: list[dict]) -> pdk.Deck:
    here = (pos["lon"], pos["lat"])
    past = [(p["lon"], p["lat"]) for p in track if p["timestamp"] <= pos["timestamp"]] + [here]
    future = [here] + [(p["lon"], p["lat"]) for p in track if p["timestamp"] > pos["timestamp"]]
    point = [{"lon": pos["lon"], "lat": pos["lat"]}]
    layers = [
        pdk.Layer("PathLayer", data=[{"path": s} for s in split_at_antimeridian(past)],
                  get_path="path", get_color=[120, 170, 255, 150], width_min_pixels=2),
        pdk.Layer("PathLayer", data=[{"path": s} for s in split_at_antimeridian(future)],
                  get_path="path", get_color=[255, 165, 60, 230], width_min_pixels=3),
        pdk.Layer("ScatterplotLayer", data=point, get_position=["lon", "lat"],
                  get_radius=max(pos["footprint_km"], 1) * 500,     # footprint is a diameter in km
                  get_fill_color=[255, 255, 255, 25], stroked=True,
                  get_line_color=[255, 255, 255, 120], line_width_min_pixels=1),
        pdk.Layer("ScatterplotLayer", data=point, get_position=["lon", "lat"],
                  get_radius=1, radius_min_pixels=7, get_fill_color=[255, 70, 70, 255]),
    ]
    return pdk.Deck(layers=layers, map_style="dark",
                    initial_view_state=pdk.ViewState(latitude=10, longitude=0, zoom=0.6))


# ───────────────────────────── UI ─────────────────────────────

def render_observer_check(pos: dict) -> None:
    with st.expander("How far is the ISS from me?"):
        text = st.text_input("Your latitude, longitude", placeholder="e.g. 51.5, -0.12", key="observer")
        if not text:
            return
        obs = parse_latlon(text)
        if obs is None:
            st.warning("Enter two numbers like `51.5, -0.12` (latitude -90 to 90, longitude -180 to 180).")
            return
        ground, slant, elev = look_angles(*obs, pos["lat"], pos["lon"], pos["altitude_km"])
        a, b, c = st.columns(3)
        a.metric("Ground distance", f"{ground:,.0f} km")
        b.metric("Line-of-sight distance", f"{slant:,.0f} km")
        c.metric("Elevation", f"{elev:.0f}°")
        if elev > 0:
            st.success("It's above your horizon right now. Seeing it also needs a dark sky for you and a sunlit station.")
        else:
            st.info("It's below your horizon right now.")


def render_person(p: dict, i: int) -> None:
    with st.container(border=True, key=f"card_person_{i}"):
        if p["image"]:
            st.image(p["image"], use_container_width=True)
        else:
            st.markdown(f'<div class="avatar">{html.escape(initials(p["name"]))}</div>', unsafe_allow_html=True)
        st.markdown(f"**{md_escape(p['name'])}**")
        meta = person_meta(p)
        if meta:
            st.caption(meta)
        facts = person_facts(p)
        if facts:
            st.markdown(facts_html(facts), unsafe_allow_html=True)
        if p["craft"]:
            st.caption(f"Aboard: {p['craft']}")
        if not facts:
            st.caption("Career details are not available in this record.")
        links = [(name, url) for name, url in
                 (("Wikipedia", p["wiki"]), ("X / Twitter", p["twitter"]), ("Instagram", p["instagram"])) if url]
        if links:
            anchors = " · ".join(
                f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
                f'{html.escape(name)}</a>' for name, url in links
            )
            st.markdown(anchors, unsafe_allow_html=True)
        if p["bio"]:
            with st.expander("Biography"):
                bio = p["bio"].strip()
                st.markdown(md_escape(bio[:2400] + ("…" if len(bio) > 2400 else "")))


def render_crew() -> None:
    st.subheader("People in space right now")
    if st.button("Refresh crew", help="Request an updated crew list. The public source has a strict rate limit."):
        get_astronauts.clear()
        st.rerun()
    try:
        people = get_astronauts()
        st.session_state["_last_astronauts"] = people
    except ApiError as e:
        people = st.session_state.get("_last_astronauts")
        if people is None:
            st.error(f"Could not load astronaut data. {e}")
            return
        st.warning(f"Showing the last successfully loaded crew list because the live request failed. {e}")
    people = sorted(people, key=lambda p: (p["group"], p["name"]))
    groups = sorted({p["group"] for p in people if p["group"]})
    filters = st.columns([1, 2])
    selected_group = filters[0].selectbox("Agency", ["All agencies", *groups], key="crew_agency")
    query = filters[1].text_input("Search crew", placeholder="Name, agency, nationality…", key="crew_search").strip().casefold()
    visible = [p for p in people
               if (selected_group == "All agencies" or p["group"] == selected_group)
               and (not query or query in " ".join((p["name"], p["agency"], p["nationality"], p["craft"], p["bio"])).casefold())]
    a, b, _ = st.columns(3)
    a.metric("In orbit now", len(people))
    b.metric("Matching crew", len(visible))
    if visible:
        st.caption(crew_summary(visible))
    if not visible:
        st.info("No crew match those filters.")
    for start in range(0, len(visible), 3):
        for k, (col, p) in enumerate(zip(st.columns(3), visible[start:start + 3])):
            with col:
                render_person(p, start + k)
    st.caption("Crew, photos and career details are supplied by Launch Library 2 and cached for one hour to respect its public rate limit. "
               "Career time in space is the total across all flights, as recorded there.")


def main() -> None:
    st.set_page_config(page_title="Space Tracker", page_icon="🛰️", layout="wide")
    eyebrow("Live telemetry")
    st.title("Live Space Tracker")
    control_cols = st.columns([1, 1, 4])
    live = control_cols[0].toggle("Live updates", value=True, help="Refresh ISS telemetry every 10 seconds.")
    refresh = control_cols[1].button("Refresh now", help="Fetch fresh ISS telemetry and orbit-track data.")
    if refresh:
        for cached in (get_iss_position, get_ground_track):
            cached.clear()
        st.rerun()

    @st.fragment(run_every=10 if live else None)
    def live_panel() -> None:
        stale = False
        try:
            pos = get_iss_position()
            st.session_state["_last_iss_position"] = pos
        except ApiError as e:
            pos = st.session_state.get("_last_iss_position")
            if pos is None:
                st.error(f"Could not load ISS position. {e}")
                return
            stale = True
            age_s = max(0, int(time.time() - pos["timestamp"]))
            st.warning(f"Live telemetry is unavailable. Showing the last successful position from {fmt_hours(age_s)} ago. {e}")
        if not stale:
            age_s = max(0, int(time.time() - pos["timestamp"]))
            if age_s > 90:
                st.warning(f"The latest position is {fmt_hours(age_s)} old; the feed may be delayed.")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Altitude", f"{pos['altitude_km']:.0f} km")
        c2.metric("Speed", f"{pos['velocity_kmh']:,.0f} km/h")
        c3.metric("Orbit period", f"{orbital_period_min(pos['altitude_km']):.0f} min")
        c4.metric("Lighting", pos["visibility"].title())
        c5.metric("Over", f"{pos['lat']:.1f}°, {pos['lon']:.1f}°")

        try:
            track_anchor = int(pos["timestamp"] // 300 * 300)
            track = get_ground_track(track_anchor)
        except ApiError as e:
            track = []
            st.warning(f"Orbit track unavailable, showing position only. {e}")
        st.pydeck_chart(build_map(pos, track), height=520)
        updated = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(pos["timestamp"]))
        st.caption(f"Blue: previous 45 min. Orange: next 45 min. Circle: approximate area that can see the ISS above the horizon. "
                   f"Telemetry timestamp: {updated}.")
        render_observer_check(pos)

    live_panel()
    with st.expander("Data sources and assumptions"):
        st.markdown(
            "ISS position and orbit-track points come from [Where the ISS at?](https://ord.wheretheiss.at/w/developer). "
            "Crew profiles come from [Launch Library 2](https://ll.thespacedevs.com/docs). The crew response is cached "
            "for one hour to stay within the public service's rate limit."
        )
        st.caption(
            "The observer calculation treats Earth as a sphere with a 6,371 km mean radius. It ignores terrain, "
            "buildings, clouds, and local light pollution; a positive elevation means the ISS is geometrically above "
            "the horizon, not that it is guaranteed to be visible."
        )
    render_crew()


if __name__ == "__main__":
    main()
