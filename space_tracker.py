"""Live Space Tracker: crew in orbit, live ISS position, orbit track, and a 'how far is it from me' check."""
from __future__ import annotations

import html
import math
import re
import time
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

import pydeck as pdk
import streamlit as st

from common import ApiError, fmt_latlon, get_json, location_input, md_escape, safe_url, valid_latlon
from theme import eyebrow, facts_html

LL2_URL = "https://ll.thespacedevs.com/2.3.0/astronauts/"
ISS_URL = "https://api.wheretheiss.at/v1/satellites/25544"
ISS_POSITIONS_URL = ISS_URL + "/positions"

EARTH_R_KM = 6371.0
MU_EARTH = 398600.4418   # km^3/s^2


# ───────────────────────────── Pure helpers (no I/O) ─────────────────────────────

def orbital_period_min(altitude_km: float) -> float:
    """Period of a circular orbit at the given altitude."""
    if not math.isfinite(altitude_km) or altitude_km <= 0:
        raise ValueError("Altitude must be a positive, finite number of kilometres.")
    a = EARTH_R_KM + altitude_km
    return 2 * math.pi * math.sqrt(a**3 / MU_EARTH) / 60


def look_angles(obs_lat: float, obs_lon: float, sat_lat: float, sat_lon: float, alt_km: float):
    """Return (ground distance km, straight-line distance km, elevation above horizon in degrees)."""
    if not valid_latlon(obs_lat, obs_lon) or not valid_latlon(sat_lat, sat_lon):
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


def next_pass(obs: tuple[float, float], track: list[dict], alt_km: float, now_ts: int) -> dict | None:
    """First run of future track points above the observer's horizon: {'start', 'end'} timestamps and 'peak' elevation."""
    start = end = None
    peak = -90.0
    for p in track:
        if p["timestamp"] <= now_ts:
            continue
        el = look_angles(*obs, p["lat"], p["lon"], alt_km)[2]
        if el > 0:
            start, end, peak = start or p["timestamp"], p["timestamp"], max(peak, el)
        elif start:
            break
    return {"start": start, "end": end, "peak": peak} if start else None


def subsolar_point(ts: float) -> tuple[float, float]:
    """(lat, lon) where the Sun is directly overhead, from NOAA's low-precision solar equations (good to ~0.5°)."""
    t = datetime.fromtimestamp(ts, timezone.utc)
    g = 2 * math.pi / 365 * (t.timetuple().tm_yday - 1 + (t.hour - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))     # minutes
    lon = -(t.hour * 60 + t.minute + t.second / 60 + eqtime - 720) / 4
    return math.degrees(decl), (lon + 180) % 360 - 180


def night_polygon(ts: float, step: int = 2) -> list[list[float]]:
    """[lon, lat] outline of the night side of Earth (clamped to ±85° so the Mercator map can draw it)."""
    lat_s, lon_s = subsolar_point(ts)
    d = math.radians(lat_s)
    if abs(d) < 1e-4:                               # equinox: terminator runs pole to pole
        d = math.copysign(1e-4, d or 1)
    edge = []
    for lon in range(-180, 181, step):
        lat = math.degrees(math.atan(-math.cos(math.radians(lon - lon_s)) / math.tan(d)))
        edge.append([lon, max(-85.0, min(85.0, lat))])
    pole = -85.0 if lat_s > 0 else 85.0             # the pole tilted away from the Sun is in darkness
    return edge + [[180, pole], [-180, pole]]


def sun_elevation(lat: float, lon: float, ts: float) -> float:
    """Sun's angle above the horizon at a place (degrees; negative is night)."""
    s_lat, s_lon = subsolar_point(ts)
    p1, p2 = math.radians(lat), math.radians(s_lat)
    cos_c = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(math.radians(lon - s_lon))
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cos_c))))


def sky_words(sun_elev: float) -> str:
    if sun_elev > 0:
        return "Daytime"
    if sun_elev > -6:
        return "Twilight"
    return "Dark" if sun_elev <= -12 else "Getting dark"


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

@st.cache_data(ttl=3600)   # long cache: the free API is rate-limited
def get_astronauts() -> list[dict]:
    data = get_json(LL2_URL, {"in_space": "true", "limit": 100}, service="The astronaut service")
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ApiError("The astronaut service returned an unexpected response.")
    people = [parse_astronaut(a) for a in data["results"] if isinstance(a, dict)]
    if data["results"] and not people:
        raise ApiError("The astronaut service returned no readable crew records.")
    return people


@st.cache_data(ttl=5)
def get_iss_position() -> dict:
    d = get_json(ISS_URL, service="The ISS position service", timeout=10)
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
    if (not valid_latlon(pos["lat"], pos["lon"])
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
        data = get_json(ISS_POSITIONS_URL, {"timestamps": ",".join(map(str, chunk)), "units": "kilometers"},
                        service="The ISS track service", timeout=10)
        if not isinstance(data, list):
            raise ApiError("The ISS track service returned an unexpected response.")
        try:
            for point in data:
                lat, lon = float(point["latitude"]), float(point["longitude"])
                stamp = int(point["timestamp"])
                if not valid_latlon(lat, lon) or stamp <= 0:
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

def build_map(pos: dict, track: list[dict], me: tuple[float, float] | None = None) -> pdk.Deck:
    here = (pos["lon"], pos["lat"])
    past = [(p["lon"], p["lat"]) for p in track if p["timestamp"] <= pos["timestamp"]] + [here]
    future = [here] + [(p["lon"], p["lat"]) for p in track if p["timestamp"] > pos["timestamp"]]
    point = [{"lon": pos["lon"], "lat": pos["lat"], "label": "ISS"}]
    layers = [
        pdk.Layer("PolygonLayer", data=[{"polygon": night_polygon(pos["timestamp"])}], get_polygon="polygon",
                  get_fill_color=[0, 4, 20, 110], stroked=False),
        pdk.Layer("PathLayer", data=[{"path": s} for s in split_at_antimeridian(past)],
                  get_path="path", get_color=[120, 170, 255, 150], width_min_pixels=2),
        pdk.Layer("PathLayer", data=[{"path": s} for s in split_at_antimeridian(future)],
                  get_path="path", get_color=[255, 165, 60, 230], width_min_pixels=3),
        pdk.Layer("ScatterplotLayer", data=point, get_position=["lon", "lat"],
                  get_radius=max(pos["footprint_km"], 1) * 500,     # footprint is a diameter in km
                  get_fill_color=[255, 255, 255, 25], stroked=True,
                  get_line_color=[255, 255, 255, 120], line_width_min_pixels=1),
        pdk.Layer("ScatterplotLayer", data=point, get_position=["lon", "lat"], pickable=True,
                  get_radius=1, radius_min_pixels=7, get_fill_color=[255, 70, 70, 255]),
    ]
    if me:
        layers.append(pdk.Layer("ScatterplotLayer", data=[{"lat": me[0], "lon": me[1], "label": "You"}],
                                get_position=["lon", "lat"], pickable=True, get_radius=1, radius_min_pixels=6,
                                get_fill_color=[110, 168, 255, 255], stroked=True,
                                get_line_color=[255, 255, 255, 255], line_width_min_pixels=2))
    return pdk.Deck(layers=layers, map_style="dark", tooltip={"text": "{label}"},
                    initial_view_state=pdk.ViewState(latitude=10, longitude=0, zoom=0.6))


# ───────────────────────────── UI ─────────────────────────────

def mins_from_now(ts: int, now_ts: int) -> str:
    m = max(1, round((ts - now_ts) / 60))
    return f"{m} min"


def render_observer_check(pos: dict, track: list[dict]) -> tuple[float, float] | None:
    """'Where is it relative to me?' panel. Returns the observer's location (for the map pin) if one was entered."""
    with st.expander("Where is the ISS relative to me?", expanded=bool(st.session_state.get("observer"))):
        obs = location_input("observer")
        if obs is None:
            st.caption("Enter your location to see the distance, elevation and whether it passes over you in the next 45 minutes.")
            return None
        ground, slant, elev = look_angles(*obs, pos["lat"], pos["lon"], pos["altitude_km"])
        sun = sun_elevation(*obs, pos["timestamp"])
        a, b, c, d = st.columns(4)
        a.metric("Ground distance", f"{ground:,.0f} km")
        b.metric("Line-of-sight distance", f"{slant:,.0f} km")
        c.metric("ISS elevation", f"{elev:.0f}°", help="Angle above your horizon. Below 0° it is out of sight.")
        d.metric("Your sky", sky_words(sun), help=f"The Sun is {sun:.0f}° above your horizon.")
        now = pos["timestamp"]
        if elev > 0:
            sets = next((p["timestamp"] for p in track if p["timestamp"] > now
                         and look_angles(*obs, p["lat"], p["lon"], pos["altitude_km"])[2] <= 0), None)
            when = f" It drops below your horizon in about {mins_from_now(sets, now)}." if sets else ""
            if pos["visibility"] == "daylight" and sun < -6:
                st.success(f"It's overhead now, sunlit, and your sky is dark: look up, you can see it!{when}")
            else:
                st.success(f"It's above your horizon right now.{when} To see it you need a dark sky and a sunlit station.")
        else:
            nxt = next_pass(obs, track, pos["altitude_km"], now)
            if nxt:
                st.info(f"It rises above your horizon in about {mins_from_now(nxt['start'], now)}, "
                        f"reaching roughly {nxt['peak']:.0f}° high, then sets about {mins_from_now(nxt['end'], now)} from now.")
            else:
                st.info("It's below your horizon and won't pass over you in the next 45 minutes.")
        return obs


def render_person(p: dict, i: int) -> None:
    with st.container(border=True, key=f"card_person_{i}"):
        if p["image"]:
            st.image(p["image"], width="stretch")
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
        c4.metric("Lighting", "Sunlit" if pos["visibility"] == "daylight" else pos["visibility"].title(),
                  help="Whether the station is in sunlight (it can only be seen from the ground when sunlit) or in Earth's shadow.")
        c5.metric("Over", fmt_latlon(pos["lat"], pos["lon"]))

        try:
            track_anchor = int(pos["timestamp"] // 300 * 300)
            with st.spinner("Plotting the orbit…"):
                track = get_ground_track(track_anchor)
        except ApiError as e:
            track = []
            st.warning(f"Orbit track unavailable, showing position only. {e}")
        me = render_observer_check(pos, track)
        st.pydeck_chart(build_map(pos, track, me), height=520)
        updated = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(pos["timestamp"]))
        st.caption(f"Blue: previous 45 min. Orange: next 45 min. Circle: approximate area that can see the ISS above the horizon. "
                   f"Shaded: night side of Earth. Telemetry timestamp: {updated}.")

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
