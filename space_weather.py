"""Space Weather & Aurora Live: storm level, solar wind, flares, an aurora map with a 'can I see it?' check, and NOAA alerts."""
from __future__ import annotations

import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import altair as alt
import pandas as pd
import pydeck as pdk
import streamlit as st

from common import ApiError, get_json as _get_json, location_input
from theme import eyebrow, facts_html

SWPC = "https://services.swpc.noaa.gov"
KP_URL = SWPC + "/products/noaa-planetary-k-index-forecast.json"      # observed + estimated + 3-day predicted, 3-hourly
KP_1M_URL = SWPC + "/json/planetary_k_index_1m.json"                   # 1-minute estimated Kp
WIND_URL = SWPC + "/json/rtsw/rtsw_wind_1m.json"                       # last 24 h, newest first, several spacecraft
MAG_URL = SWPC + "/json/rtsw/rtsw_mag_1m.json"
XRAY_URL = SWPC + "/json/goes/primary/xrays-7-day.json"
FLARE_URL = SWPC + "/json/goes/primary/xray-flares-latest.json"
AURORA_URL = SWPC + "/json/ovation_aurora_latest.json"
ALERTS_URL = SWPC + "/products/alerts.json"

SDO = "https://sdo.gsfc.nasa.gov/assets/img/latest/latest_1024_{}.jpg"
SUN_VIEWS = {
    "Corona · AIA 193 Å": ("0193", "Hot coronal plasma; dark patches are coronal holes that feed fast solar wind."),
    "Flares · AIA 131 Å": ("0131", "The hottest flaring material; flares light up here first."),
    "Loops · AIA 171 Å": ("0171", "Quiet corona and the magnetic loops above sunspots."),
    "Chromosphere · AIA 304 Å": ("0304", "The layer above the surface; prominences and filaments show up well."),
    "Visible light · HMI": ("HMIIC", "What the Sun looks like through a solar filter; sunspots are the dark marks."),
    "Magnetogram · HMI": ("HMIBC", "Surface magnetism. Tangled black and white regions are where flares come from."),
}
CORONAGRAPH = "https://soho.nascom.nasa.gov/data/realtime/c3/1024/latest.jpg"

G_SCALE = [(9, "G5 Extreme"), (8, "G4 Severe"), (7, "G3 Strong"), (6, "G2 Moderate"), (5, "G1 Minor")]


# ───────────────────────────── Pure helpers (no I/O) ─────────────────────────────

def parse_time(s: Any) -> datetime | None:
    """NOAA time tags, with or without a trailing Z or fractional seconds -> aware UTC datetime."""
    try:
        t = datetime.fromisoformat(str(s).strip().replace("Z", "").replace(" ", "T"))
    except ValueError:
        return None
    return t.replace(tzinfo=timezone.utc)


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def kp_level(kp: float) -> str:
    """Kp -> NOAA geomagnetic storm level in words."""
    for floor, name in G_SCALE:
        if kp >= floor:
            return name
    return "Active" if kp >= 4 else "Unsettled" if kp >= 3 else "Quiet"


def flare_class(flux: float | None) -> str:
    """GOES 0.1-0.8 nm X-ray flux (W/m²) -> flare class such as 'C3.4'."""
    if flux is None or flux <= 0:
        return ""
    for letter, base in (("X", 1e-4), ("M", 1e-5), ("C", 1e-6), ("B", 1e-7)):
        if flux >= base:
            return f"{letter}{flux / base:.1f}"
    return f"A{flux / 1e-8:.1f}"


def aurora_verdict(kp_now: float, kp_peak: float, bz: float | None) -> tuple[str, str, str]:
    """(level, headline, detail) for the next 24 hours. level is 'success', 'info' or 'warning' for the alert style."""
    peak = max(kp_now, kp_peak)
    south = bz is not None and bz <= -5
    if peak >= 7:
        head, where, level = "Major aurora display possible", "far from the poles, as far as ~45° magnetic latitude (e.g. southern Europe, much of the US)", "warning"
    elif peak >= 5:
        head, where, level = "Aurora likely at mid-high latitudes", "Scotland, Scandinavia, southern Canada and the northern US states", "success"
    elif peak >= 4:
        head, where, level = "Aurora likely tonight at high latitudes", "Iceland, northern Scandinavia, Alaska and northern Canada", "success"
    elif peak >= 3:
        head, where, level = "Aurora possible at high latitudes", "inside or near the Arctic and Antarctic circles", "info"
    else:
        head, where, level = "Quiet. Aurora stays near the poles", "only the auroral ovals over the polar regions", "info"
    detail = f"Kp now {kp_now:.1f} ({kp_level(kp_now)}); forecast peak over the next 24 h is Kp {peak:.1f} ({kp_level(peak)}). Best bet: {where}."
    if south:
        detail += f" The magnetic field (Bz {bz:+.1f} nT) points south right now, which lets solar-wind energy in and can brighten the display quickly."
    elif bz is not None and bz >= 5:
        detail += f" Bz is northward ({bz:+.1f} nT), which tends to hold activity back."
    return level, head, detail + " You still need a dark, clear sky away from city lights."


def wind_note(speed: float | None) -> str:
    if speed is None:
        return ""
    if speed >= 700:
        return "very fast"
    if speed >= 500:
        return "fast"
    return "slow" if speed < 350 else "normal"


def lookup_aurora(grid: dict[tuple[int, int], int], lat: float, lon: float) -> int:
    """Nearest OVATION grid value (grid keyed by (lon 0-359, lat -90..90))."""
    return grid.get((round(lon) % 360, max(-90, min(90, round(lat)))), 0)


def chance_words(p: int) -> str:
    if p >= 50:
        return "high"
    if p >= 20:
        return "good"
    if p >= 5:
        return "low"
    return "very low"


# ───────────────────────────── API clients ─────────────────────────────

def get_json(url: str) -> Any:
    return _get_json(url, service="NOAA's space-weather service")


def _rows(url: str) -> list[dict]:
    data = get_json(url)
    if not isinstance(data, list):
        raise ApiError("NOAA returned an unexpected response.")
    return [r for r in data if isinstance(r, dict)]


@st.cache_data(ttl=300, show_spinner=False)
def get_kp() -> pd.DataFrame:
    out = []
    for r in _rows(KP_URL):
        t, kp = parse_time(r.get("time_tag")), _num(r.get("kp"))
        if t and kp is not None:
            out.append({"time": t, "Kp": kp, "status": str(r.get("observed") or "observed").title()})
    if not out:
        raise ApiError("The Kp feed had no readable values.")
    return pd.DataFrame(out).sort_values("time")


@st.cache_data(ttl=60, show_spinner=False)
def get_kp_now() -> float | None:
    rows = [r for r in _rows(KP_1M_URL) if _num(r.get("estimated_kp")) is not None]
    return _num(rows[-1]["estimated_kp"]) if rows else None


@st.cache_data(ttl=60, show_spinner=False)
def get_solar_wind() -> pd.DataFrame:
    """Merged 1-minute plasma + magnetic field from the spacecraft NOAA currently marks active."""
    def frame(url: str, fields: dict[str, str]) -> pd.DataFrame:
        recs = {}
        for r in _rows(url):
            t = parse_time(r.get("time_tag"))
            if t and r.get("active") is True:
                recs[t] = {name: _num(r.get(key)) for name, key in fields.items()}
        return pd.DataFrame.from_dict(recs, orient="index")
    wind = frame(WIND_URL, {"Speed (km/s)": "proton_speed", "Density (p/cm³)": "proton_density"})
    mag = frame(MAG_URL, {"Bz (nT)": "bz_gsm", "Bt (nT)": "bt"})
    df = wind.join(mag, how="outer").sort_index()
    if df.empty:
        raise ApiError("The solar-wind feed had no readable values.")
    df.index.name = "time"
    return df


@st.cache_data(ttl=60, show_spinner=False)
def get_xrays() -> pd.DataFrame:
    out = []
    for r in _rows(XRAY_URL):
        t, f = parse_time(r.get("time_tag")), _num(r.get("flux"))
        if t and f and f > 0 and r.get("energy") == "0.1-0.8nm":
            out.append({"time": t, "flux": f})
    if not out:
        raise ApiError("The X-ray feed had no readable values.")
    return pd.DataFrame(out).sort_values("time")


@st.cache_data(ttl=60, show_spinner=False)
def get_latest_flare() -> dict:
    rows = _rows(FLARE_URL)
    return rows[0] if rows else {}


@st.cache_data(ttl=300, show_spinner=False)
def get_aurora() -> dict:
    d = get_json(AURORA_URL)
    if not isinstance(d, dict) or not isinstance(d.get("coordinates"), list):
        raise ApiError("The aurora forecast returned an unexpected response.")
    grid: dict[tuple[int, int], int] = {}
    for c in d["coordinates"]:
        try:
            lon, lat, p = int(c[0]), int(c[1]), int(c[2])
        except (TypeError, ValueError, IndexError):
            continue
        if p > 0:
            grid[(lon % 360, lat)] = p
    return {"grid": grid, "observed": parse_time(d.get("Observation Time")), "forecast": parse_time(d.get("Forecast Time"))}


@st.cache_data(ttl=300, show_spinner=False)
def get_alerts() -> list[dict]:
    out = []
    for r in _rows(ALERTS_URL):
        msg = str(r.get("message") or "").replace("\r\n", "\n").strip()
        t = parse_time(r.get("issue_datetime"))
        if msg and t:
            out.append({"time": t, "id": str(r.get("product_id") or ""), "message": msg})
    return sorted(out, key=lambda a: a["time"], reverse=True)


def alert_title(message: str) -> str:
    """First ALERT/WATCH/WARNING/SUMMARY line of a NOAA message, or a fallback."""
    for line in message.splitlines():
        if re.match(r"^(CONTINUED ALERT|ALERT|WATCH|WARNING|SUMMARY|EXTENDED WARNING|CANCEL)", line.strip()):
            return line.strip()
    return "Space weather message"


# ───────────────────────────── Map ─────────────────────────────

def aurora_color(p: int) -> list[int]:
    """Green (faint) -> yellow -> red (intense), with alpha rising with probability."""
    t = min(1.0, p / 60)
    if t < 0.5:
        r, g, b = int(60 + 400 * t), 230, int(120 * (1 - 2 * t))
    else:
        r, g, b = 255, int(230 - 360 * (t - 0.5)), 40
    return [r, g, b, int(70 + 170 * t)]


def build_aurora_map(grid: dict[tuple[int, int], int], me: tuple[float, float] | None) -> pdk.Deck:
    cells = [{"lon": lon - 360 if lon > 180 else lon, "lat": lat, "p": p, "color": aurora_color(p)}
             for (lon, lat), p in grid.items() if p >= 3]
    layers = [pdk.Layer("ScatterplotLayer", data=cells, get_position=["lon", "lat"], get_radius=62_000,
                        get_fill_color="color", pickable=True)]
    if me:
        layers.append(pdk.Layer("ScatterplotLayer", data=[{"lat": me[0], "lon": me[1]}], get_position=["lon", "lat"],
                                get_radius=1, radius_min_pixels=7, get_fill_color=[110, 168, 255, 255],
                                stroked=True, get_line_color=[255, 255, 255, 255], line_width_min_pixels=2))
    return pdk.Deck(layers=layers, map_style="dark", tooltip={"text": "Aurora chance: {p}%"},
                    initial_view_state=pdk.ViewState(latitude=me[0] if me else 45, longitude=me[1] if me else -20, zoom=0.8))


# ───────────────────────────── UI ─────────────────────────────

def ago(t: datetime | None) -> str:
    if t is None:
        return ""
    mins = max(0, int((datetime.now(timezone.utc) - t).total_seconds() // 60))
    return f"{mins} min ago" if mins < 120 else f"{mins // 60} h ago"


def render_now() -> None:
    def attempt(fetch, fallback, warn: bool = False):
        try:
            return fetch()
        except ApiError as e:
            if warn:
                st.warning(str(e))
            return fallback

    kp = attempt(get_kp, None, warn=True)
    kp_now = attempt(get_kp_now, None)
    wind = attempt(get_solar_wind, None, warn=True)
    flare = attempt(get_latest_flare, {})
    xr = attempt(get_xrays, None)

    observed = kp[kp["status"] != "Predicted"] if kp is not None else None
    if kp_now is None and observed is not None and not observed.empty:
        kp_now = float(observed["Kp"].iloc[-1])
    latest = wind.ffill().iloc[-1] if wind is not None else None
    speed = _num(latest["Speed (km/s)"]) if latest is not None else None
    bz = _num(latest["Bz (nT)"]) if latest is not None else None
    current = str(flare.get("current_class") or "") or (flare_class(float(xr["flux"].iloc[-1])) if xr is not None else "")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Kp index", f"{kp_now:.1f}" if kp_now is not None else "n/a", kp_level(kp_now) if kp_now is not None else None,
              delta_color="off", delta_arrow="off", help="Planetary geomagnetic activity, 0 (calm) to 9 (extreme storm). Kp 5+ is a storm.")
    c2.metric("Solar wind", f"{speed:,.0f} km/s" if speed is not None else "n/a", wind_note(speed) or None, delta_color="off", delta_arrow="off",
              help="Speed of the solar wind measured at L1, about 1.5 million km sunward. Typical is ~400 km/s.")
    c3.metric("Bz", f"{bz:+.1f} nT" if bz is not None else "n/a",
              ("southward: aurora-friendly" if bz < 0 else "northward") if bz is not None else None,
              delta_color="normal" if bz is not None and bz < 0 else "off", delta_arrow="off",
              help="North-south part of the solar wind's magnetic field. Strongly negative (south) drives auroras.")
    c4.metric("X-ray flux", current or "n/a", "flare" if current[:1] in ("M", "X") else "background" if current else None,
              delta_color="off", delta_arrow="off", help="GOES X-ray class: A, B, C (small), M (medium), X (major). Each letter is 10× the last.")

    if kp_now is not None and kp is not None:
        now = datetime.now(timezone.utc)
        ahead = kp[(kp["time"] >= now - timedelta(hours=3)) & (kp["time"] <= now + timedelta(hours=24))]
        peak = float(ahead["Kp"].max()) if not ahead.empty else kp_now
        level, head, detail = aurora_verdict(kp_now, peak, bz)
        getattr(st, level)(f"**{head}.** {detail}")

    rows = []
    if flare.get("max_class"):
        when = parse_time(flare.get("max_time"))
        rows.append(("Latest flare peak", f"{flare['max_class']} · {when:%d %b %H:%M} UTC" if when else str(flare["max_class"])))
    if xr is not None:
        day = xr[xr["time"] >= xr["time"].iloc[-1] - timedelta(hours=24)]
        rows.append(("Strongest X-ray, last 24 h", flare_class(float(day["flux"].max()))))
    if latest is not None:
        dens, bt = _num(latest["Density (p/cm³)"]), _num(latest["Bt (nT)"])
        if dens is not None:
            rows.append(("Solar-wind density", f"{dens:.1f} protons/cm³"))
        if bt is not None:
            rows.append(("Field strength Bt", f"{bt:.1f} nT"))
        rows.append(("Solar-wind reading", ago(wind.index[-1].to_pydatetime())))
    if rows:
        st.markdown(facts_html(rows), unsafe_allow_html=True)


def render_aurora() -> None:
    st.subheader("Aurora forecast")
    try:
        aur = get_aurora()
    except ApiError as e:
        st.error(f"Could not load the aurora forecast. {e}")
        return
    me = location_input("aurora_me", placeholder="e.g. 64.1, -21.9  (Reykjavík)")
    if me:
        p = lookup_aurora(aur["grid"], *me)
        a, b = st.columns([1, 3])
        a.metric("Aurora chance overhead", f"{p}%", chance_words(p), delta_color="off", delta_arrow="off")
        with b:
            if p >= 20:
                st.success("Good odds if it is dark and clear where you are. Look toward the pole, away from city lights.")
            elif p >= 5:
                st.info("Possible but faint. A camera on a long exposure often picks up more than your eyes.")
            else:
                st.info("Unlikely right now. The aurora may still be visible low on the horizon if the oval is close to you.")
    st.pydeck_chart(build_aurora_map(aur["grid"], me), height=500)
    when = aur["forecast"]
    st.caption("NOAA OVATION model: probability of seeing aurora overhead, for about 30-90 min ahead. "
               "Green is faint, red is strong. " + (f"Valid for {when:%d %b %H:%M} UTC." if when else ""))


def render_sun() -> None:
    st.subheader("The Sun right now")
    view = st.selectbox("View", list(SUN_VIEWS), key="sun_view")
    code, note = SUN_VIEWS[view]
    bust = int(time.time() // 900)                      # new image URL every 15 min so browsers fetch the update
    a, b = st.columns(2)
    with a:
        st.image(f"{SDO.format(code)}?t={bust}", width="stretch")
        st.caption(f"NASA SDO · {note}")
    with b:
        st.image(f"{CORONAGRAPH}?t={bust}", width="stretch")
        st.caption("SOHO LASCO C3 coronagraph. The Sun is blocked out; bright clouds leaving it are coronal mass ejections (CMEs), "
                   "which can trigger storms 1-4 days later.")


def _time_chart(df: pd.DataFrame, y: str, color: str, title: str, log: bool = False, zero: bool = False) -> alt.Chart:
    scale = alt.Scale(type="log") if log else alt.Scale(zero=False)
    base = alt.Chart(df).mark_line(color=color, strokeWidth=1.6).encode(
        x=alt.X("time:T", title=None), y=alt.Y(f"{y}:Q", title=title, scale=scale),
        tooltip=[alt.Tooltip("time:T", format="%d %b %H:%M"), alt.Tooltip(f"{y}:Q", format=".2f")])
    if zero:
        base += alt.Chart(pd.DataFrame({"z": [0]})).mark_rule(color="#8b95a7", strokeDash=[4, 4]).encode(y="z:Q")
    return base.properties(height=220)


def render_charts() -> None:
    st.subheader("History")
    day, week = st.tabs(["Past day", "Past week"])
    with day:
        try:
            w = get_solar_wind().reset_index()
            w.columns = ["time", "speed", "density", "bz", "bt"]
            a, b = st.columns(2)
            a.altair_chart(_time_chart(w.dropna(subset=["speed"]), "speed", "#ff9f43", "Solar wind (km/s)"), width="stretch")
            b.altair_chart(_time_chart(w.dropna(subset=["bz"]), "bz", "#6ea8ff", "Bz (nT)", zero=True), width="stretch")
            st.caption("1-minute data from the active NOAA solar-wind spacecraft at L1. Long stretches of negative Bz with fast wind are what build storms.")
        except ApiError as e:
            st.warning(f"Solar-wind history unavailable. {e}")
    with week:
        try:
            kp = get_kp()
            colors = alt.Scale(domain=["Observed", "Estimated", "Predicted"], range=["#6ea8ff", "#a8c8ff", "#ff9f43"])
            bars = alt.Chart(kp).mark_bar(width={"band": 0.85}).encode(
                x=alt.X("time:T", title=None), y=alt.Y("Kp:Q", scale=alt.Scale(domain=[0, 9]), title="Kp"),
                color=alt.Color("status:N", scale=colors, legend=alt.Legend(orient="top", title=None)),
                tooltip=[alt.Tooltip("time:T", format="%d %b %H:%M"), "Kp:Q", "status:N"])
            storm = alt.Chart(pd.DataFrame({"y": [5]})).mark_rule(color="#ff6b6b", strokeDash=[4, 4]).encode(y="y:Q")
            st.altair_chart((bars + storm).properties(height=240), width="stretch")
            st.caption("3-hourly Kp: the past week plus NOAA's 3-day forecast. Above the red line (Kp 5) is a geomagnetic storm.")
        except ApiError as e:
            st.warning(f"Kp history unavailable. {e}")
        try:
            xr = get_xrays()
            xr = xr.iloc[::5]                               # 5-minute resolution is plenty for a week
            ticks = pd.DataFrame({"y": [1e-6, 1e-5, 1e-4], "label": ["C", "M", "X"]})
            rules = alt.Chart(ticks).mark_rule(color="#5d6778", strokeDash=[2, 4]).encode(y="y:Q")
            labels = alt.Chart(ticks).mark_text(align="left", dx=4, dy=-6, color="#8b95a7").encode(y="y:Q", text="label:N", x=alt.value(0))
            st.altair_chart(_time_chart(xr, "flux", "#ffd166", "X-ray flux (W/m²)", log=True) + rules + labels, width="stretch")
            st.caption("GOES long-wave X-rays. Spikes are solar flares; crossing the M line can cause radio blackouts on Earth's dayside.")
        except ApiError as e:
            st.warning(f"X-ray history unavailable. {e}")


def render_alerts() -> None:
    st.subheader("NOAA alerts & watches")
    try:
        alerts = get_alerts()
    except ApiError as e:
        st.warning(f"Alerts unavailable. {e}")
        return
    recent = [a for a in alerts if a["time"] >= datetime.now(timezone.utc) - timedelta(days=3)]
    if not recent:
        st.info("No alerts, watches or warnings issued in the past three days.")
        return
    st.caption(f"{len(recent)} messages in the past three days, newest first.")
    for a in recent[:12]:
        with st.expander(f"{a['time']:%d %b %H:%M} UTC · {alert_title(a['message'])}"):
            st.text(a["message"])


def main() -> None:
    st.set_page_config(page_title="Space Weather", page_icon="🌌", layout="wide")
    eyebrow("Live from NOAA SWPC")
    st.title("Space Weather & Aurora")
    cols = st.columns([1, 1, 4])
    live = cols[0].toggle("Live updates", value=True, help="Refresh the readings every minute.")
    if cols[1].button("Refresh now"):
        st.cache_data.clear()
        st.rerun()

    @st.fragment(run_every=60 if live else None)
    def live_panel() -> None:
        render_now()

    live_panel()
    render_aurora()
    render_sun()
    render_charts()
    render_alerts()
    with st.expander("Data sources"):
        st.markdown(
            "Kp, solar wind, X-rays, the OVATION aurora forecast and alerts come from the "
            "[NOAA Space Weather Prediction Center](https://www.swpc.noaa.gov/) (U.S. public domain, no API key). "
            "Sun images are from [NASA SDO](https://sdo.gsfc.nasa.gov/) and the ESA/NASA "
            "[SOHO](https://soho.nascom.nasa.gov/) coronagraph. Solar wind refreshes every minute, the aurora forecast "
            "every five minutes, and the official Kp every three hours."
        )
        st.caption("The 'aurora likely' verdict is a rule of thumb based on Kp and Bz, not an official NOAA forecast.")


if __name__ == "__main__":
    main()
