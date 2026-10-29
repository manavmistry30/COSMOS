"""Space Flight Simulator: plan an Earth-to-destination trip with real orbital mechanics, then watch it fly."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

# ───────────────────────────── Constants ─────────────────────────────

G0 = 9.80665                    # m/s^2
AU_KM = 149_597_870.7
MU_SUN = 1.32712440018e11       # km^3/s^2
MU_EARTH = 398_600.4418         # km^3/s^2
R_EARTH_KM = 6_378.137
R_LEO_KM = R_EARTH_KM + 400     # 400 km parking orbit
C_KMS = 299_792.458
KG_PER_PERSON_DAY = 5.0         # rough food + water + oxygen per astronaut per day, no recycling
ISS_MASS_KG = 420_000
SATURN_V_LEO_KG = 140_000

ENGINES = {
    "Chemical: hydrolox (Isp 450 s)": 450,
    "Chemical: methalox (Isp 370 s)": 370,
    "Nuclear thermal (Isp 900 s)": 900,
    "Ion drive (Isp 3,000 s, low thrust)": 3000,
}

# ───────────────────────────── Destination data ─────────────────────────────
# central: "sun" (a_au) or "earth" (a_km). mu/radius_km describe the destination for the capture burn.

DESTS = {
    "moon": dict(name="Moon", emoji="🌕", color="#d0d0d0", central="earth", a_km=384_400,
                 mu=4_902.8, radius_km=1_737.4, capture_r_km=1_737.4 + 100,
                 gravity=1.62, temp_min_c=-173, temp_max_c=127, pressure_bar=0.0, has_surface=True,
                 hazards=["lunar dust", "no magnetosphere", "no atmosphere"],
                 fact="The Moon drifts away from Earth by about 3.8 cm every year."),
    "mercury": dict(name="Mercury", emoji="☿️", color="#b5b5b5", central="sun", a_au=0.387,
                    mu=22_031.86, radius_km=2_439.7, capture_r_km=2_439.7 + 400,
                    gravity=3.70, temp_min_c=-180, temp_max_c=430, pressure_bar=0.0, has_surface=True,
                    hazards=["no magnetosphere", "no atmosphere"],
                    fact="A day on Mercury (sunrise to sunrise) lasts 176 Earth days."),
    "venus": dict(name="Venus", emoji="♀️", color="#e8c28a", central="sun", a_au=0.723,
                  mu=324_858.6, radius_km=6_051.8, capture_r_km=6_051.8 + 400,
                  gravity=8.87, temp_min_c=464, temp_max_c=464, pressure_bar=92.0, has_surface=True,
                  hazards=["sulfuric acid clouds", "CO2 atmosphere"],
                  fact="Venus is hotter than Mercury despite being farther from the Sun."),
    "mars": dict(name="Mars", emoji="🔴", color="#e5593a", central="sun", a_au=1.524,
                 mu=42_828.4, radius_km=3_389.5, capture_r_km=3_389.5 + 400,
                 gravity=3.71, temp_min_c=-153, temp_max_c=20, pressure_bar=0.006, has_surface=True,
                 hazards=["global dust storms", "no magnetosphere", "CO2 atmosphere", "perchlorate soil"],
                 fact="Mars dust storms can cover the whole planet for weeks."),
    "jupiter": dict(name="Jupiter", emoji="🟠", color="#d9a066", central="sun", a_au=5.203,
                    mu=126_686_534.0, radius_km=71_492, capture_r_km=20 * 71_492,
                    gravity=24.79, temp_min_c=-110, temp_max_c=-110, pressure_bar=1.0, has_surface=False,
                    hazards=["lethal radiation belts", "no solid surface"],
                    fact="The Great Red Spot is a storm wider than Earth."),
    "saturn": dict(name="Saturn", emoji="🪐", color="#e3cf9a", central="sun", a_au=9.537,
                   mu=37_931_207.8, radius_km=60_268, capture_r_km=20 * 60_268,
                   gravity=10.44, temp_min_c=-140, temp_max_c=-140, pressure_bar=1.0, has_surface=False,
                   hazards=["no solid surface", "high winds"],
                   fact="Saturn is less dense than water."),
    "uranus": dict(name="Uranus", emoji="🔵", color="#8fe3e8", central="sun", a_au=19.19,
                   mu=5_793_939.0, radius_km=25_559, capture_r_km=20 * 25_559,
                   gravity=8.69, temp_min_c=-195, temp_max_c=-195, pressure_bar=1.0, has_surface=False,
                   hazards=["no solid surface"],
                   fact="Uranus rotates on its side, tilted about 98 degrees."),
    "neptune": dict(name="Neptune", emoji="🔷", color="#4f6df5", central="sun", a_au=30.07,
                    mu=6_836_529.0, radius_km=24_764, capture_r_km=20 * 24_764,
                    gravity=11.15, temp_min_c=-201, temp_max_c=-201, pressure_bar=1.0, has_surface=False,
                    hazards=["no solid surface", "fastest winds in the solar system"],
                    fact="Neptune was found by mathematical prediction before it was seen."),
    "pluto": dict(name="Pluto", emoji="🤍", color="#c9b8a8", central="sun", a_au=39.48,
                  mu=869.6, radius_km=1_188.3, capture_r_km=1_188.3 + 500,
                  gravity=0.62, temp_min_c=-240, temp_max_c=-218, pressure_bar=0.00001, has_surface=True,
                  hazards=["extreme cold", "nitrogen-ice surface"],
                  fact="Pluto's heart-shaped glacier is made of nitrogen ice."),
}

ORBITS = [("Mercury", 0.387, "#b5b5b5"), ("Venus", 0.723, "#e8c28a"), ("Earth", 1.0, "#4aa3ff"),
          ("Mars", 1.524, "#e5593a"), ("Jupiter", 5.203, "#d9a066"), ("Saturn", 9.537, "#e3cf9a"),
          ("Uranus", 19.19, "#8fe3e8"), ("Neptune", 30.07, "#4f6df5"), ("Pluto", 39.48, "#c9b8a8")]
STATIC_ANGLES = {"Mercury": 2.3, "Venus": 5.0, "Earth": 0.0, "Mars": 3.9, "Jupiter": 1.9,
                 "Saturn": 4.4, "Uranus": 0.8, "Neptune": 5.6, "Pluto": 2.9}

# Real missions: (name, launch, arrival, what happened). Dates from memory; check before quoting.
REAL_MISSIONS = {
    "moon": ("Apollo 11", date(1969, 7, 16), date(1969, 7, 19), "lunar orbit",
             "A higher-energy burn than Hohmann gets crews there in about 3 days."),
    "venus": ("Mariner 2", date(1962, 8, 27), date(1962, 12, 14), "flyby",
              "A bit more fuel than Hohmann buys a noticeably shorter trip."),
    "mars": ("Perseverance", date(2020, 7, 30), date(2021, 2, 18), "landing",
             "A slightly faster-than-Hohmann trajectory, timed for the launch window."),
    "mercury": ("MESSENGER", date(2004, 8, 3), date(2011, 3, 18), "orbit",
                "Falling toward the Sun is fast, but slowing down enough to orbit took six flybys of Earth, Venus and Mercury."),
    "jupiter": ("Juno", date(2011, 8, 5), date(2016, 7, 5), "orbit",
                "It looped back for an Earth gravity assist, trading time for a much smaller rocket."),
    "saturn": ("Cassini", date(1997, 10, 15), date(2004, 7, 1), "orbit",
               "Gravity assists from Venus (twice), Earth and Jupiter saved fuel but took years."),
    "uranus": ("Voyager 2", date(1977, 8, 20), date(1986, 1, 24), "flyby",
               "Jupiter and Saturn gravity assists made it far faster than a Hohmann transfer."),
    "neptune": ("Voyager 2", date(1977, 8, 20), date(1989, 8, 25), "flyby",
                "Three gravity assists cut a ~30-year Hohmann trip to about 12 years."),
    "pluto": ("New Horizons", date(2006, 1, 19), date(2015, 7, 14), "flyby",
              "The fastest launch ever, plus a Jupiter gravity assist, beat Hohmann by decades."),
}


# ───────────────────────────── Orbital mechanics (pure) ─────────────────────────────

@dataclass(frozen=True)
class Trip:
    mu: float                  # central body, km^3/s^2
    r1_km: float               # departure orbit radius
    r2_km: float               # destination orbit radius
    transfer_s: float
    depart_dv: float           # burn from 400 km low Earth orbit, km/s
    capture_dv: float          # burn into a circular orbit at the destination, km/s
    v_inf_arrival: float       # speed relative to the destination on arrival, km/s
    origin_period_s: float
    target_period_s: float
    phase_deg: float           # how far ahead (+) or behind (-) of the origin the target must be at launch
    window_days: float         # time between launch opportunities

    @property
    def transfer_days(self) -> float:
        return self.transfer_s / 86_400


def plan_trip(dest: dict) -> Trip:
    """Idealised Hohmann transfer: circular, coplanar orbits, instant burns."""
    if dest["central"] == "earth":
        mu, r1, r2 = MU_EARTH, R_LEO_KM, dest["a_km"]
    else:
        mu, r1, r2 = MU_SUN, AU_KM, dest["a_au"] * AU_KM
    transfer_s = math.pi * math.sqrt(((r1 + r2) / 2) ** 3 / mu)
    v1, v2 = math.sqrt(mu / r1), math.sqrt(mu / r2)
    dv_dep = abs(v1 * (math.sqrt(2 * r2 / (r1 + r2)) - 1))
    dv_arr = abs(v2 * (1 - math.sqrt(2 * r1 / (r1 + r2))))
    if dest["central"] == "earth":
        leo_dv = dv_dep
    else:
        v_leo = math.sqrt(MU_EARTH / R_LEO_KM)
        leo_dv = math.sqrt(dv_dep**2 + 2 * v_leo**2) - v_leo      # escape to a hyperbolic excess of dv_dep
    rc = dest["capture_r_km"]
    capture = math.sqrt(dv_arr**2 + 2 * dest["mu"] / rc) - math.sqrt(dest["mu"] / rc)
    p1 = 2 * math.pi * math.sqrt(r1**3 / mu)
    p2 = 2 * math.pi * math.sqrt(r2**3 / mu)
    phase = math.degrees(math.pi - 2 * math.pi / p2 * transfer_s)
    phase = (phase + 180) % 360 - 180
    window = abs(1 / (1 / p1 - 1 / p2)) / 86_400 if dest["central"] == "sun" else p2 / 86_400
    return Trip(mu, r1, r2, transfer_s, leo_dv, capture, dv_arr, p1, p2, phase, window)


def kepler_E(M: float, e: float) -> float:
    E = M if e < 0.8 else math.pi
    for _ in range(60):
        step = (E - e * math.sin(E) - M) / (1 - e * math.cos(E))
        E -= step
        if abs(step) < 1e-13:
            break
    return E


def transfer_state(trip: Trip, t_s: float) -> tuple[float, float, float]:
    """Spacecraft (radius km, polar angle rad, speed km/s) t seconds after departure. Departure point is angle 0."""
    r1, r2 = trip.r1_km, trip.r2_km
    a, e = (r1 + r2) / 2, abs(r2 - r1) / (r1 + r2)
    outward = r2 > r1
    frac = min(max(t_s / trip.transfer_s, 0.0), 1.0)
    M = math.pi * frac + (0.0 if outward else math.pi)
    E = kepler_E(M, e)
    nu = 2 * math.atan2(math.sqrt(1 + e) * math.sin(E / 2), math.sqrt(1 - e) * math.cos(E / 2))
    theta = nu if outward else nu - math.pi
    r = a * (1 - e * math.cos(E))
    speed = math.sqrt(trip.mu * (2 / r - 1 / a))
    return r, theta, speed


def transfer_curve(trip: Trip, n: int = 180) -> list[tuple[float, tuple[float, float]]]:
    """Points along the transfer orbit as (fraction of trip flown, (x, y) km). Sampled evenly in eccentric
    anomaly, which keeps very elongated orbits (like the Moon's) smooth near the planet."""
    r1, r2 = trip.r1_km, trip.r2_km
    a, e = (r1 + r2) / 2, abs(r2 - r1) / (r1 + r2)
    outward = r2 > r1
    e0, m0 = (0.0, 0.0) if outward else (math.pi, math.pi)
    pts = []
    for i in range(n + 1):
        E = e0 + math.pi * i / n
        nu = 2 * math.atan2(math.sqrt(1 + e) * math.sin(E / 2), math.sqrt(1 - e) * math.cos(E / 2))
        theta = nu if outward else nu - math.pi
        r = a * (1 - e * math.cos(E))
        pts.append(((E - e * math.sin(E) - m0) / math.pi, (r * math.cos(theta), r * math.sin(theta))))
    return pts


def snapshot(trip: Trip, t_s: float, central: str) -> dict:
    """Positions (km, inertial frame) and distances t seconds into the trip."""
    r, theta, speed = transfer_state(trip, t_s)
    sc = (r * math.cos(theta), r * math.sin(theta))
    if central == "sun":
        ang = 2 * math.pi / trip.origin_period_s * t_s
        origin = (trip.r1_km * math.cos(ang), trip.r1_km * math.sin(ang))
    else:
        origin = (0.0, 0.0)
    n2 = 2 * math.pi / trip.target_period_s
    tang = math.radians(trip.phase_deg) + n2 * t_s
    target = (trip.r2_km * math.cos(tang), trip.r2_km * math.sin(tang))
    return {"sc": sc, "origin": origin, "target": target, "speed": speed,
            "earth_dist": math.dist(sc, origin), "target_dist": math.dist(sc, target)}


def propellant_plan(payload_kg: float, dv_kms: float, isp_s: float) -> tuple[float, float, float]:
    """Tsiolkovsky rocket equation: (mass ratio, propellant kg, total mass kg). Ignores tank mass."""
    ratio = math.exp(min(dv_kms / (isp_s * G0 / 1000), 50))
    prop = payload_kg * (ratio - 1)
    return ratio, prop, payload_kg + prop


# ───────────────────────────── Packing rules ─────────────────────────────

def _has(p: dict, word: str) -> bool:
    return any(word in h for h in p["hazards"])


RULES = [
    (lambda p, d: not p["has_surface"], "Orbit-only mission profile", "No solid surface to land on"),
    (lambda p, d: p["has_surface"] and p["temp_max_c"] > 100, "Active-cooling suit + thermal shielding", "Surface gets hotter than 100 °C"),
    (lambda p, d: p["has_surface"] and p["temp_min_c"] < -50, "Heated suit layers", "Surface gets colder than -50 °C"),
    (lambda p, d: p["has_surface"] and p["pressure_bar"] < 0.01, "Pressurised suit + oxygen supply", "Surface pressure under 1% of Earth's"),
    (lambda p, d: p["has_surface"] and p["pressure_bar"] > 10, "Pressure-hardened lander hull", "Surface pressure above 10 bar"),
    (lambda p, d: _has(p, "acid"), "Acid-resistant coatings", "Corrosive cloud and atmosphere chemistry"),
    (lambda p, d: _has(p, "CO2"), "Own breathable air supply", "Atmosphere is mostly CO2"),
    (lambda p, d: _has(p, "dust"), "Sealed filters and dust-proof seals", "Fine, abrasive dust (storms, lunar regolith)"),
    (lambda p, d: _has(p, "radiation") or _has(p, "no magnetosphere"), "Radiation shelter / storm shielding", "No protective magnetic field, or intense radiation belts"),
    (lambda p, d: _has(p, "perchlorate"), "Soil-contact decontamination kit", "Toxic perchlorates in surface soil"),
    (lambda p, d: p["has_surface"] and p["gravity"] < 5, "Resistance-exercise gear", "Low gravity accelerates bone and muscle loss"),
    (lambda p, d: p["has_surface"] and p["gravity"] > 12, "Powered exoskeleton", "Gravity above 1.2 g"),
    (lambda p, d: d > 200, "Long-duration food, water recycling, spares", "Transit longer than 200 days"),
    (lambda p, d: d > 200, "Crew psychology and comms plan", "Long isolation and light-lag delays"),
]


def packing_list(planet: dict, travel_days: float) -> list[tuple[str, str]]:
    return [(item, why) for cond, item, why in RULES if cond(planet, travel_days)]


# ───────────────────────────── Formatting ─────────────────────────────

def fmt_km(km: float) -> str:
    if km < 1e6:
        return f"{km:,.0f} km"
    if km < 1e9:
        return f"{km / 1e6:,.1f} million km"
    return f"{km / 1e9:,.2f} billion km"


def fmt_delay(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} s"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} h"


def fmt_window(days: float) -> str:
    return f"{days:.0f} days" if days < 60 else f"{days / 30.44:.0f} months"


def fmt_mass(kg: float) -> str:
    return f"{kg / 1000:,.0f} t" if kg >= 10_000 else f"{kg:,.0f} kg"


# ───────────────────────────── Orbit diagram (SVG) ─────────────────────────────

def orrery_svg(trip: Trip, dest: dict, t_s: float) -> str:
    size, c, view = 640, 320, 285
    sun_central = dest["central"] == "sun"
    k = view / math.sqrt(max(trip.r1_km, trip.r2_km) * 1.12)

    def pt(x: float, y: float) -> tuple[float, float]:
        r = math.hypot(x, y)
        if r == 0:
            return c, c
        rp = k * math.sqrt(r)
        return c + rp * x / r, c - rp * y / r

    def ring(r_km: float, color: str = "#26304f", dash: str = "") -> str:
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        return f'<circle cx="{c}" cy="{c}" r="{k * math.sqrt(r_km):.1f}" fill="none" stroke="{color}" stroke-width="1"{extra}/>'

    def path(points: list[tuple[float, float]]) -> str:
        return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)

    def label(x: float, y: float, text: str, color: str, bold: bool = False, anchor: str = "start", s: int = 13) -> str:
        w = ' font-weight="bold"' if bold else ""
        return f'<text x="{x:.1f}" y="{y:.1f}" fill="{color}" font-size="{s}" text-anchor="{anchor}"{w}>{text}</text>'

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}" font-family="sans-serif">',
           '<defs><radialGradient id="sun"><stop offset="0%" stop-color="#fff3b0"/><stop offset="55%" stop-color="#ffb703"/>'
           '<stop offset="100%" stop-color="#ffb703" stop-opacity="0"/></radialGradient></defs>',
           f'<rect width="{size}" height="{size}" rx="16" fill="#070b18"/>']
    rng = random.Random(7)
    for _ in range(90):
        out.append(f'<circle cx="{rng.uniform(8, size - 8):.0f}" cy="{rng.uniform(8, size - 8):.0f}" '
                   f'r="{rng.uniform(0.4, 1.3):.1f}" fill="#fff" opacity="{rng.uniform(0.25, 0.8):.2f}"/>')

    snap = snapshot(trip, t_s, dest["central"])
    if sun_central:
        out.append(f'<circle cx="{c}" cy="{c}" r="22" fill="url(#sun)"/>')
        for name, a_au, color in ORBITS:
            r_km = a_au * AU_KM
            if r_km <= max(trip.r1_km, trip.r2_km) * 1.12:
                out.append(ring(r_km))
                if name not in ("Earth", dest["name"]):
                    x, y = pt(r_km * math.cos(STATIC_ANGLES[name]), r_km * math.sin(STATIC_ANGLES[name]))
                    out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}" opacity="0.8"/>')
                    out.append(label(x + 7, y - 6, name, "#6b7796", s=10))
    else:
        out.append(ring(trip.r1_km))
        out.append(ring(trip.r2_km))
        out.append(f'<circle cx="{c}" cy="{c}" r="9" fill="#4aa3ff"/>')
        out.append(label(c, c + 24, "Earth", "#4aa3ff", True, "middle"))
        lx, ly = pt(trip.r1_km, 0)
        out.append(label(lx + 6, ly - 6, "LEO", "#6b7796", s=10))

    cur = min(max(t_s / trip.transfer_s, 0.0), 1.0)
    curve = transfer_curve(trip)
    pts = [pt(*xy) for _, xy in curve]
    flown = [pt(*xy) for frac, xy in curve if frac <= cur] + [pt(*snap["sc"])]
    out.append(f'<path d="{path(pts)}" fill="none" stroke="#ff9f1c" stroke-width="2" stroke-dasharray="5 5" opacity="0.45"/>')
    if len(flown) > 1:
        out.append(f'<path d="{path(flown)}" fill="none" stroke="#ff9f1c" stroke-width="3"/>')

    tx, ty = pt(*snap["target"])
    out.append(f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="7" fill="{dest["color"]}" stroke="#fff" stroke-width="1.5"/>')
    out.append(label(tx + 11, ty + 4, dest["name"], "#ffffff", True))
    if sun_central:
        ex, ey = pt(*snap["origin"])
        out.append(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="6" fill="#4aa3ff" stroke="#fff" stroke-width="1.5"/>')
        out.append(label(ex + 10, ey - 8, "Earth", "#4aa3ff", True))
    sx, sy = pt(*snap["sc"])
    out.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="5" fill="#fff" stroke="#ff9f1c" stroke-width="2.5"/>')

    out.append('<line x1="18" y1="22" x2="44" y2="22" stroke="#ff9f1c" stroke-width="3"/>')
    out.append(label(50, 26, "Spacecraft path", "#c8d0e8", s=12))
    out.append('<line x1="18" y1="40" x2="44" y2="40" stroke="#ff9f1c" stroke-width="2" stroke-dasharray="5 5" opacity="0.6"/>')
    out.append(label(50, 44, "Still to fly", "#c8d0e8", s=12))
    out.append(label(size - 14, size - 12, "Square-root distance scale; other planets are schematic", "#6b7796", anchor="end", s=11))
    out.append("</svg>")
    return "".join(out)


# ───────────────────────────── Real-mission comparison ─────────────────────────────

def real_days(key: str) -> int | None:
    m = REAL_MISSIONS.get(key)
    return (m[2] - m[1]).days if m else None


def missions_table() -> pd.DataFrame:
    rows = []
    for key, dest in DESTS.items():
        m = REAL_MISSIONS.get(key)
        if not m:
            continue
        ideal = plan_trip(dest).transfer_days
        rows.append({"Destination": dest["name"], "Real mission": f"{m[0]} ({m[1].year})", "Reached": m[3],
                     "Real trip (days)": real_days(key), "Ideal Hohmann (days)": round(ideal),
                     "Real ÷ ideal": round(real_days(key) / ideal, 2)})
    return pd.DataFrame(rows)


# ───────────────────────────── State and callbacks ─────────────────────────────

ss = st.session_state


def init_state() -> None:
    ss.setdefault("dest", "mars")
    ss.setdefault("mission", "Orbit the destination")
    ss.setdefault("engine", next(iter(ENGINES)))
    ss.setdefault("crew", 4)
    ss.setdefault("dry_t", 20.0)
    ss.setdefault("clock", 0)
    ss.setdefault("playing", False)


def reset_clock() -> None:
    ss.clock, ss.playing = 0, False


def toggle_play() -> None:
    if not ss.playing and ss.clock >= 100:
        ss.clock = 0
    ss.playing = not ss.playing


# ───────────────────────────── UI ─────────────────────────────

def render_flight(trip: Trip, dest: dict) -> None:
    st.button("⏸ Pause" if ss.playing else "▶ Play the mission", key="play", on_click=toggle_play)

    @st.fragment(run_every=0.4 if ss.playing else None)
    def panel() -> None:
        if ss.playing:
            ss.clock = min(100, ss.clock + 2)
        st.slider("Mission clock", 0, 100, key="clock", format="%d%%")
        t_s = ss.clock / 100 * trip.transfer_s
        snap = snapshot(trip, t_s, dest["central"])
        left, right = st.columns([3, 2])
        left.image(orrery_svg(trip, dest, t_s))
        elapsed = t_s / 86_400
        right.metric("Mission elapsed", f"{elapsed:,.1f} days" if trip.transfer_days < 30 else f"{elapsed:,.0f} days")
        right.metric("Distance from Earth", fmt_km(snap["earth_dist"]))
        right.metric("Signal delay (one way)", fmt_delay(snap["earth_dist"] / C_KMS))
        right.metric(f"Distance to {dest['name']}", fmt_km(snap["target_dist"]))
        right.metric("Speed vs. " + ("the Sun" if dest["central"] == "sun" else "Earth"), f"{snap['speed']:.1f} km/s")
        if ss.clock >= 100:
            st.success(f"🎉 Arrived at {dest['name']} after {trip.transfer_days:,.0f} days.")
        if ss.playing and ss.clock >= 100:
            ss.playing = False
            st.rerun()

    panel()
    side = "ahead of" if trip.phase_deg > 0 else "behind"
    origin = "the departure point in low Earth orbit" if dest["central"] == "earth" else "Earth"
    st.caption(f"Launch window: {dest['name']} must be {abs(trip.phase_deg):.0f}° {side} {origin} at departure, "
               f"so it arrives at the same moment you do. Windows repeat about every {fmt_window(trip.window_days)}.")


def render_fuel(trip: Trip, dest: dict, total_dv: float, capture: float) -> None:
    isp = ENGINES[ss.engine]
    days = trip.transfer_days
    consumables = ss.crew * days * KG_PER_PERSON_DAY
    payload = ss.dry_t * 1000 + consumables
    ratio, prop, launch = propellant_plan(payload, total_dv, isp)

    a, b, c, d = st.columns(4)
    a.metric("Departure burn", f"{trip.depart_dv:.2f} km/s")
    b.metric("Capture burn", f"{capture:.2f} km/s" if capture else "None (flyby)")
    c.metric("Total Δv", f"{total_dv:.2f} km/s")
    d.metric("Mass ratio", f"{ratio:,.2f}×")
    e, f, g, h = st.columns(4)
    e.metric("Crew supplies", fmt_mass(consumables))
    f.metric("Propellant needed", fmt_mass(prop))
    g.metric("Mass in low Earth orbit", fmt_mass(launch))
    h.metric("Propellant share", f"{prop / launch:.0%}")
    st.info(f"That's about **{launch / ISS_MASS_KG:.1f}×** the mass of the International Space Station, "
            f"or roughly **{math.ceil(launch / SATURN_V_LEO_KG)}** Saturn V payloads to low Earth orbit.")
    st.caption("Rocket equation with an ideal, instant burn. Tank and engine mass are ignored, so real rockets need more. "
               f"Crew supplies assume {KG_PER_PERSON_DAY:g} kg per person per day for food, water and oxygen, with no recycling. "
               f"Capture is into a circular orbit {fmt_km(dest['capture_r_km'])} from the centre of {dest['name']}.")
    if isp >= 3000:
        st.warning("Ion drives are very efficient but their thrust is tiny. Real ion trips spiral out slowly and take far "
                   "longer than a Hohmann transfer, so treat this fuel figure as a best case.")


def render_packing(trip: Trip, dest: dict) -> None:
    st.info(dest["fact"])
    for item, why in packing_list(dest, trip.transfer_days):
        st.markdown(f"- **{item}**: {why}")
    if ss.crew:
        st.markdown(f"- **Crew supplies**: about {fmt_mass(ss.crew * trip.transfer_days * KG_PER_PERSON_DAY)} of food, water and oxygen for {ss.crew} people")


def render_real(trip: Trip, key: str, dest: dict) -> None:
    m = REAL_MISSIONS.get(key)
    if m:
        real = real_days(key)
        c1, c2, c3 = st.columns(3)
        c1.metric(f"{m[0]} ({m[1].year})", f"{real:,} days", f"{m[3]}", delta_color="off")
        c2.metric("Ideal Hohmann transfer", f"{trip.transfer_days:,.0f} days")
        ratio = real / trip.transfer_days
        c3.metric("Real ÷ ideal", f"{ratio:.2f}×", "faster" if ratio < 1 else "slower", delta_color="off")
        st.write(m[4])
        st.caption(f"Launched {m[1]:%d %b %Y}, reached {dest['name']} on {m[2]:%d %b %Y}. Dates are from memory, so check them before quoting.")
    with st.expander("All destinations: real missions vs. the ideal transfer"):
        st.dataframe(missions_table(), hide_index=True)


def main() -> None:
    st.set_page_config(page_title="Space Flight Simulator", page_icon="🚀", layout="wide")
    init_state()
    st.title("🚀 Space Flight Simulator")
    st.caption("Plan a trip from low Earth orbit using real orbital mechanics, then fly it.")

    c1, c2, c3, c4, c5 = st.columns([1.3, 1.4, 1.8, 0.9, 1.1])
    c1.selectbox("Destination", list(DESTS), key="dest", on_change=reset_clock,
                 format_func=lambda k: f"{DESTS[k]['emoji']} {DESTS[k]['name']}")
    c2.radio("Mission", ["Orbit the destination", "Flyby only"], key="mission")
    c3.selectbox("Engine", list(ENGINES), key="engine")
    c4.number_input("Crew", 0, 12, key="crew")
    c5.number_input("Dry mass (tonnes)", 1.0, 500.0, step=1.0, key="dry_t")

    key = ss.dest
    dest = DESTS[key]
    trip = plan_trip(dest)
    capture = trip.capture_dv if ss.mission.startswith("Orbit") else 0.0
    total_dv = trip.depart_dv + capture

    if dest["central"] == "sun":
        delay = f"{abs(trip.r2_km - trip.r1_km) / C_KMS / 60:.0f}-{(trip.r2_km + trip.r1_km) / C_KMS / 60:.0f} min"
    else:
        delay = fmt_delay(trip.r2_km / C_KMS)
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("One-way trip", f"{trip.transfer_days:,.0f} days",
              f"{trip.transfer_days / 365.25:.1f} years" if trip.transfer_days >= 365 else None, delta_color="off")
    m2.metric("Total Δv", f"{total_dv:.2f} km/s")
    m3.metric("Light delay", delay)
    m4.metric("Launch window", f"every {fmt_window(trip.window_days)}")
    m5.metric("Gravity", f"{dest['gravity'] / G0:.2f} g")

    tab_fly, tab_fuel, tab_pack, tab_real = st.tabs(["🛰️ Fly the mission", "⛽ Fuel and mass", "🎒 Packing list", "📚 Real missions"])
    with tab_fly:
        render_flight(trip, dest)
    with tab_fuel:
        render_fuel(trip, dest, total_dv, capture)
    with tab_pack:
        render_packing(trip, dest)
    with tab_real:
        render_real(trip, key, dest)


if __name__ == "__main__":
    main()