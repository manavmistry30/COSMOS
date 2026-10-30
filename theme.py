"""Professional dark space theme: restrained palette, clean type, hairline cards, a subtle starfield and Earth-horizon glow.
Pure CSS (no extra packages). Call apply_theme() once at the top of main.py."""
from __future__ import annotations

import html
import random

import streamlit as st

ANIMATE = True   # False = completely still (the starfield no longer drifts)

FONT_IMPORT = ("@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600"
               "&family=Space+Grotesk:wght@500;600;700&display=swap');")


def _stars(seed: int, count: int, size: tuple[float, float], tile: int, alpha: tuple[float, float],
           tints: list[tuple[int, int, int]]) -> str:
    """One tile of stars as stacked radial gradients. The tile repeats, so a drift of exactly one tile loops seamlessly."""
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        x, y = rng.randint(6, tile - 6), rng.randint(6, tile - 6)
        s = round(rng.uniform(*size), 1)
        r, g, b = rng.choice(tints)
        out.append(f"radial-gradient({s}px {s}px at {x}px {y}px, rgba({r},{g},{b},{rng.uniform(*alpha):.2f}) 45%, transparent 100%)")
    return ",".join(out)


FAR_TILE, NEAR_TILE = 640, 760

CSS = """
:root {
  --bg: #04060b; --text: #f2f5fa; --muted: #8b95a7; --faint: #5d6778;
  --line: rgba(255, 255, 255, 0.09); --line-2: rgba(255, 255, 255, 0.20);
  --surface: rgba(14, 19, 31, 0.72); --surface-2: rgba(22, 29, 46, 0.80);
  --accent: #6ea8ff; --warm: #ff9f43;
  --display: 'Space Grotesk', 'Inter', system-ui, sans-serif;
  --body: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;
}

/* ============ background: deep space, faint stars, Earth-limb glow ============ */
.stApp {
  background:
    radial-gradient(1200px 540px at 50% -10%, rgba(70, 110, 255, 0.17), transparent 62%),
    radial-gradient(760px 420px at 100% 0%, rgba(140, 90, 255, 0.08), transparent 60%),
    var(--bg);
  background-attachment: fixed;
}
.stApp::before, .stApp::after {
  content: ""; position: fixed; left: 0; top: 0; width: 100%; pointer-events: none; z-index: 0;
}
.stApp::before {
  height: calc(100% + __FAR_TILE__px);
  background-image: __FAR__;
  background-size: __FAR_TILE__px __FAR_TILE__px;
  animation: drift-far 360s linear infinite;
}
.stApp::after {
  height: calc(100% + __NEAR_TILE__px);
  background-image: __NEAR__;
  background-size: __NEAR_TILE__px __NEAR_TILE__px;
  animation: drift-near 220s linear infinite, twinkle 7s ease-in-out infinite alternate;
}
@keyframes drift-far  { to { transform: translate3d(0, -__FAR_TILE__px, 0); } }
@keyframes drift-near { to { transform: translate3d(0, -__NEAR_TILE__px, 0); } }
@keyframes twinkle    { from { opacity: 0.55; } to { opacity: 1; } }

div[data-testid="stElementContainer"]:has(.fx), .element-container:has(.fx) {
  position: absolute; height: 0; margin: 0; overflow: visible;
}
.fx > * { pointer-events: none; }
.limb {
  position: fixed; z-index: -1; left: 50%; width: 190vw; height: 190vw; border-radius: 50%;
  bottom: calc(-190vw + 11vh); transform: translateX(-50%);
  background: radial-gradient(circle at 50% 0%, #0a1226 0%, #050812 45%, #04060b 70%);
  box-shadow:
    0 -1px 0 rgba(190, 220, 255, 0.38),
    0 -8px 40px rgba(110, 168, 255, 0.28),
    0 -30px 110px rgba(70, 120, 255, 0.18),
    inset 0 10px 50px rgba(140, 190, 255, 0.16);
}

/* ============ page chrome ============ */
[data-testid="stAppViewContainer"] { z-index: 1; background: transparent; }
[data-testid="stMain"] { background: transparent; }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stDecoration"], footer { display: none !important; }
[data-testid="stSidebar"] {
  background: rgba(5, 8, 15, 0.82); backdrop-filter: blur(16px); border-right: 1px solid var(--line);
}
[data-testid="stSidebarHeader"]::before {
  content: "COSMOS"; font: 600 0.92rem/1 var(--display); letter-spacing: 0.36em; color: #ffffff; padding-left: 0.5rem;
}
[data-testid="stMainBlockContainer"] { max-width: 1240px !important; margin: 0 auto; padding: 4.2rem 2.4rem 6rem !important; }

/* ============ type ============ */
p, li, label, input, textarea, button, summary, [role="tab"], [data-testid="stCaptionContainer"] { font-family: var(--body); }
.eyebrow { font: 600 0.74rem/1 var(--body); letter-spacing: 0.22em; text-transform: uppercase; color: var(--accent); margin: 0 0 0.9rem; }
h1 {
  font-family: var(--display) !important; font-weight: 700 !important; color: #ffffff !important;
  font-size: clamp(2.1rem, 4.2vw, 3.4rem) !important; letter-spacing: -0.035em !important; line-height: 1.04 !important;
  padding: 0 0 0.5rem !important;
}
h2, h3 {
  font-family: var(--display) !important; font-weight: 600 !important; color: #ffffff !important;
  font-size: clamp(1.25rem, 2vw, 1.65rem) !important; letter-spacing: -0.02em !important; line-height: 1.2 !important;
}
h4 { font: 600 0.76rem/1.2 var(--body) !important; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted) !important; }
[data-testid="stCaptionContainer"] { color: var(--muted) !important; }

/* ============ widgets ============ */
[data-testid="stMetric"] {
  background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 0.95rem 1.05rem;
  transition: border-color 0.2s ease, background 0.2s ease, transform 0.2s ease;
}
[data-testid="stMetric"]:hover { border-color: var(--line-2); background: var(--surface-2); transform: translateY(-2px); }
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] p {
  font: 500 0.72rem/1.2 var(--body) !important; letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted) !important;
}
[data-testid="stMetricValue"], [data-testid="stMetricValue"] > div {
  font-family: var(--display) !important; font-weight: 600 !important; color: #ffffff !important;
  font-size: clamp(1.25rem, 1.6vw, 1.8rem) !important; letter-spacing: -0.02em; line-height: 1.15 !important;
  font-variant-numeric: tabular-nums;
}
[data-testid="stMetricValue"] * { white-space: normal !important; overflow: visible !important; text-overflow: clip !important; overflow-wrap: anywhere; }

[class*="st-key-card"] {
  background: rgba(10, 15, 26, 0.93); border-color: var(--line) !important; border-radius: 16px !important;
  transition: transform 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease, background 0.25s ease;
}
[class*="st-key-card"]:hover {
  transform: translateY(-4px); border-color: var(--line-2) !important; background: rgba(15, 22, 38, 0.96);
  box-shadow: 0 22px 60px rgba(0, 0, 0, 0.5);
}

[data-testid="stButton"] button, [data-testid="stLinkButton"] a {
  background: var(--surface) !important; border: 1px solid rgba(255, 255, 255, 0.16) !important; border-radius: 10px !important;
  color: #e8edf7 !important; font-weight: 500 !important; min-height: 2.45rem; height: auto;
  transition: background 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
}
[data-testid="stButton"] button p, [data-testid="stLinkButton"] a p {
  white-space: normal !important; overflow: visible !important; text-overflow: clip !important; line-height: 1.2 !important;
  font-size: 0.9rem !important;
}
[data-testid="stButton"] button:hover, [data-testid="stLinkButton"] a:hover {
  background: var(--surface-2) !important; border-color: rgba(255, 255, 255, 0.34) !important; color: #ffffff !important;
}
[data-testid="stButton"] button:disabled { opacity: 0.4; }
[data-testid="stBaseButton-primary"] { background: var(--accent) !important; border-color: var(--accent) !important; color: #04060b !important; }

[data-testid="stSelectbox"] [role="group"], [data-testid="stTextInputRootElement"], [data-testid="stNumberInputContainer"] {
  background: var(--surface) !important; border: 1px solid var(--line) !important; border-radius: 10px !important;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
[data-testid="stSelectbox"] [role="group"]:hover { border-color: var(--line-2) !important; }
[data-testid="stSelectbox"] [role="group"]:focus-within, [data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stNumberInputContainer"]:focus-within { border-color: var(--accent) !important; box-shadow: 0 0 0 3px rgba(110, 168, 255, 0.18); }
[data-testid="stWidgetLabel"] p { font: 500 0.78rem/1.2 var(--body) !important; letter-spacing: 0.06em; color: var(--muted) !important; }

[role="tablist"] { border-bottom: 1px solid var(--line); gap: 0.4rem; }
[data-testid="stTab"] { padding: 0.55rem 0.9rem !important; transition: color 0.2s ease; }
[data-testid="stTab"] p { font: 500 0.95rem/1.2 var(--body) !important; color: var(--muted); }
[data-testid="stTab"]:hover p, [data-testid="stTab"][aria-selected="true"] p { color: #ffffff; }

[data-testid="stSidebarNavLink"] { border-radius: 10px !important; font-weight: 500; transition: background 0.2s ease; }
[data-testid="stSidebarNavLink"]:hover { background: rgba(255, 255, 255, 0.06) !important; }
[data-testid="stSidebarNavLink"][aria-current="page"] { background: rgba(110, 168, 255, 0.14) !important; }

[data-testid="stImage"] img { border-radius: 10px; transition: transform 0.4s ease; }
[data-testid="stImage"] img:hover { transform: scale(1.02); }
/* uniform grid thumbnails: crop to one shape so card rows line up */
[class*="st-key-card_img"] [data-testid="stImage"] img, [class*="st-key-card_rel"] [data-testid="stImage"] img {
  width: 100%; aspect-ratio: 4 / 3; object-fit: cover;
}
[class*="st-key-card_person"] [data-testid="stImage"] img { width: 100%; aspect-ratio: 4 / 5; object-fit: cover; object-position: top; }
[data-testid="stDeckGlJsonChart"], [data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
[data-testid="stExpander"] details { border: 1px solid var(--line) !important; border-radius: 12px !important; background: var(--surface); }
[data-testid="stExpander"] summary p { font-weight: 500; }
[data-testid="stAlert"] { border-radius: 12px; }
hr { border-color: var(--line) !important; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.16); border-radius: 6px; }
::-webkit-scrollbar-track { background: transparent; }

/* ============ custom blocks ============ */
.facts { margin: 0.2rem 0 0.6rem; font-family: var(--body); }
.fact { display: flex; justify-content: space-between; gap: 1rem; padding: 0.5rem 0; border-bottom: 1px solid var(--line); font-size: 0.86rem; }
.fact:last-child { border-bottom: 0; }
.fact span:first-child { color: var(--muted); }
.fact span:last-child { color: #e8edf7; font-weight: 500; text-align: right; }
.avatar {
  aspect-ratio: 4 / 5; display: flex; align-items: center; justify-content: center; border-radius: 10px;
  font: 600 3rem/1 var(--display); letter-spacing: 0.04em; color: rgba(255, 255, 255, 0.82);
  background: radial-gradient(circle at 30% 20%, rgba(110, 168, 255, 0.28), rgba(110, 168, 255, 0.04) 60%), var(--surface);
  border: 1px solid var(--line);
}
"""

STILL = """
.stApp::before, .stApp::after { animation: none !important; }
"""

FX_HTML = '<div class="fx"><div class="limb"></div></div>'


def build_css(animate: bool = True, font_import: str | None = None) -> str:
    far = _stars(11, 85, (1.0, 1.5), FAR_TILE, (0.25, 0.60), [(255, 255, 255), (205, 222, 255)])
    near = _stars(23, 14, (1.6, 2.4), NEAR_TILE, (0.45, 0.85), [(255, 255, 255), (190, 212, 255), (255, 236, 205)])
    css = (CSS.replace("__FAR__", far).replace("__NEAR__", near)
           .replace("__FAR_TILE__", str(FAR_TILE)).replace("__NEAR_TILE__", str(NEAR_TILE)))
    reduced = f"@media (prefers-reduced-motion: reduce) {{ {STILL} }}"      # respect the visitor's OS setting
    return (font_import or FONT_IMPORT) + css + (reduced if animate else STILL)


def apply_theme(animate: bool = ANIMATE) -> None:
    """Call once at the top of main.py, before st.navigation(...).run()."""
    st.markdown(f"<style>{build_css(animate)}</style>{FX_HTML}", unsafe_allow_html=True)


def eyebrow(text: str) -> None:
    """Small accent label above a page title."""
    st.markdown(f'<div class="eyebrow">{html.escape(text)}</div>', unsafe_allow_html=True)


def facts_html(rows: list[tuple[str, str]]) -> str:
    """Spec-sheet rows (label left, value right). Values are escaped, so API text can't inject markup."""
    body = "".join(f'<div class="fact"><span>{html.escape(k)}</span><span>{html.escape(v)}</span></div>' for k, v in rows if v)
    return f'<div class="facts">{body}</div>'