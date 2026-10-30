"""NASA Image Explorer: search, filter, browse and dig into NASA's public image library."""
from __future__ import annotations

import html
import math
import os
import random
import re
from collections import Counter
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import streamlit as st

from common import ApiError, get_json as _get_json, md_escape, safe_url
from theme import eyebrow, facts_html

API = "https://images-api.nasa.gov"
PAGE_SIZE = 24
MAX_HITS = 10_000            # the API will not page past this many results
MAX_PAGE_SIZE = 100
MAX_QUERY_CHARS = 160
MIN_YEAR, THIS_YEAR = 1920, date.today().year
DEFAULT_QUERY = "galaxy"

TOPICS = ["Pillars of Creation", "Mars rover", "Black hole", "Apollo 11", "Saturn rings",
          "Aurora from space", "Webb telescope", "Earthrise", "Spacewalk", "Solar eclipse"]
CENTERS = {
    "JPL": "Jet Propulsion Laboratory", "GSFC": "Goddard Space Flight Center",
    "JSC": "Johnson Space Center", "KSC": "Kennedy Space Center",
    "MSFC": "Marshall Space Flight Center", "ARC": "Ames Research Center",
    "GRC": "Glenn Research Center", "AFRC": "Armstrong Flight Research Center",
    "SSC": "Stennis Space Center", "HQ": "NASA Headquarters",
}
SORTS = ["NASA's relevance order", "Newest first", "Oldest first"]
SIZE_NAMES = {"orig": "Original", "large": "Large", "medium": "Medium", "small": "Small"}
VARIANT_RE = re.compile(r"~(orig|large|medium|small|thumb)\.(\w+)$", re.IGNORECASE)

VIEWS = ["Search the archive", "Picture of the Day"]
APOD_URL = "https://api.nasa.gov/planetary/apod"                        # needs a key; DEMO_KEY allows ~10 calls/hour
APOD_WP_URL = "https://science.nasa.gov/wp-json/wp/v2/image-article"   # APOD's current home: open JSON, no key
APOD_FIRST = date(1995, 6, 16)
YOUTUBE_EMBED_RE = re.compile(r"^https://(?:www\.)?youtube(?:-nocookie)?\.com/embed/([\w-]{6,})")


# ───────────────────────────── Pure helpers (no I/O) ─────────────────────────────

def clean_text(s: Any) -> str:
    """Strip HTML tags and entities from NASA's free-text fields."""
    s = str(s or "")
    s = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|h[1-6])\s*>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def shorten(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def center_name(code: str) -> str:
    return f"{CENTERS[code]} ({code})" if code in CENTERS else code


def parse_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    raw_data = item.get("data")
    meta = raw_data[0] if isinstance(raw_data, list) and raw_data and isinstance(raw_data[0], dict) else {}
    links = item.get("links") if isinstance(item.get("links"), list) else []
    thumb_raw = (next((l.get("href") for l in links if isinstance(l, dict) and l.get("render") == "image" and l.get("href")), None)
                 or next((l.get("href") for l in links if isinstance(l, dict) and l.get("href")), None))
    thumb = safe_url(thumb_raw)
    nasa_id = meta.get("nasa_id")
    if not nasa_id or not thumb:
        return None
    kws = meta.get("keywords") or []
    if isinstance(kws, str):
        kws = [kws]
    elif not isinstance(kws, list):
        kws = []
    return {
        "nasa_id": str(nasa_id),
        "title": clean_text(meta.get("title")) or "Untitled",
        "description": clean_text(meta.get("description") or meta.get("description_508")),
        "date": str(meta.get("date_created") or "")[:10],
        "center": str(meta.get("center") or ""),
        "keywords": [str(k).strip() for k in kws if str(k).strip()],
        "photographer": clean_text(meta.get("photographer") or meta.get("secondary_creator")),
        "location": clean_text(meta.get("location")),
        "thumb": safe_url(thumb),
    }


def classify_assets(payload: Any) -> dict[str, str]:
    """Asset manifest -> {'orig': url, 'large': url, 'medium': url, 'small': url, 'thumb': url}."""
    out: dict[str, str] = {}
    collection = payload.get("collection") if isinstance(payload, dict) else None
    items = collection.get("items", []) if isinstance(collection, dict) else []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        href = safe_url(str(it.get("href", "")))
        m = VARIANT_RE.search(href)
        if m:
            out.setdefault(m.group(1).lower(), href)
    return out


def pick_display(assets: dict[str, str], fallback: str) -> str:
    """Best image a browser can actually show (never a TIFF original)."""
    for key in ("large", "medium", "small"):
        if key in assets:
            return assets[key]
    orig = assets.get("orig", "")
    if orig.lower().endswith((".jpg", ".jpeg", ".png")):
        return orig
    return assets.get("thumb") or fallback


def download_options(assets: dict[str, str]) -> list[tuple[str, str]]:
    out = []
    for key in ("orig", "large", "medium", "small"):
        if key in assets:
            ext = assets[key].rsplit(".", 1)[-1].lower()
            out.append((f"{SIZE_NAMES[key]} (.{ext})", assets[key]))
    return out


def age_text(d: str, today: date | None = None) -> str:
    try:
        taken = date.fromisoformat(d[:10])
    except (TypeError, ValueError):
        return ""
    days = ((today or date.today()) - taken).days
    if days < 0:
        return ""
    if days < 60:
        return f"{days} days ago"
    if days < 730:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"


def top_keywords(items: list[dict], n: int = 10, exclude: str = "") -> list[str]:
    """Keywords shared by at least two results: handy 'explore next' suggestions."""
    skip = exclude.strip().lower()
    counts: Counter = Counter()
    names: dict[str, str] = {}
    for it in items:
        seen: set[str] = set()
        for k in it["keywords"]:
            low = k.lower()
            if low in seen or low == skip or len(k) > 40:
                continue
            seen.add(low)
            names.setdefault(low, k)
            counts[low] += 1
    return [names[k] for k, c in counts.most_common(n) if c >= 2]


def sort_items(items: list[dict], mode: str) -> list[dict]:
    if mode == "Newest first":
        return sorted(items, key=lambda i: (i["date"] or "0000", i["title"].casefold()), reverse=True)
    if mode == "Oldest first":
        return sorted(items, key=lambda i: (i["date"] or "9999", i["title"].casefold()))
    return items


def page_count(total: int, page_size: int = PAGE_SIZE) -> int:
    if page_size <= 0:
        raise ValueError("Page size must be positive.")
    return max(1, math.ceil(min(total, MAX_HITS) / page_size))


def parse_apod(d: Any) -> dict | None:
    """One APOD API record -> the fields the page shows."""
    if not isinstance(d, dict) or not d.get("title") or not d.get("date"):
        return None
    try:
        day = date.fromisoformat(str(d["date"])[:10])
    except ValueError:
        return None
    url = safe_url(d.get("url"))
    m = YOUTUBE_EMBED_RE.match(url)
    return {
        "date": day,
        "title": clean_text(d.get("title")),
        "explanation": clean_text(d.get("explanation")),
        "media_type": str(d.get("media_type") or ""),
        "url": f"https://www.youtube.com/watch?v={m.group(1)}" if m else url,
        "hdurl": safe_url(d.get("hdurl")),
        "thumb": safe_url(d.get("thumbnail_url")),
        "credit": re.sub(r"\s+", " ", clean_text(d.get("copyright"))),
    }


def _apod_media_url(u: str) -> str:
    return safe_url(html.unescape(u))


def _tidy(text: str) -> str:
    """Close the gaps tag-stripping leaves before punctuation ('nebulas , which' -> 'nebulas, which')."""
    return re.sub(r"\s+([,.;:!?)])", r"\1", text).strip()


def parse_apod_post(p: Any) -> dict | None:
    """One NASA Science 'image-article' APOD post -> the same fields parse_apod returns (plus its page link)."""
    if not isinstance(p, dict):
        return None
    title = clean_text((p.get("title") or {}).get("rendered"))
    if not title.startswith("APOD"):
        return None
    try:
        day = date.fromisoformat(str(p.get("date"))[:10])
    except ValueError:
        return None
    body = str((p.get("content") or {}).get("rendered") or "")
    text = re.sub(r"\s+", " ", clean_text(body))
    explanation, credit = "", ""
    m = re.search(r"Explanation:\s*(.*?)\s+Date\b", text)
    if m:
        explanation = re.sub(r"\s+(?:Tomorrow.s picture:|Follow APOD on:|Your Sky Surprise:).*$", "", m.group(1))
        c = re.search(r"\bCredit\b\s*(.*?)\s+Authors & editors", text[m.end():])
        if c:
            credit = re.sub(r"^(?:Image\s+)?(?:Credit\s*)?(?:&\s*Copyright\s*)?:?\s*", "", c.group(1))
    videos = [_apod_media_url(u) for u in re.findall(r'<(?:iframe|video|source)\b[^>]*\bsrc="([^"]+)"', body)]
    images = [_apod_media_url(u) for u in re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', body) if "/apod/" in u]
    hd = next((_apod_media_url(u) for u in re.findall(r'<a\b[^>]*\bhref="([^"]+)"', body)
               if "/apod/" in u and re.search(r"\.(?:jpe?g|png|gif|tiff?)$", u.split("?")[0], re.I)), "")
    image = images[0].split("?")[0] if images and images[0] else ""
    if image and "assets.science.nasa.gov" in image and not image.lower().endswith(".gif"):
        image += "?w=1600&fit=clip"                 # NASA's image CDN resizes on request; originals can be 20+ MB
    video = next((v for v in videos if v), "")
    yt = YOUTUBE_EMBED_RE.match(video)
    return {
        "date": day,
        "title": re.split(r"\s[–—-]\s", title, maxsplit=1)[-1].strip(),
        "explanation": _tidy(explanation),
        "media_type": "video" if video else "image" if image else "other",
        "url": f"https://www.youtube.com/watch?v={yt.group(1)}" if yt else video or image,
        "hdurl": hd,
        "thumb": image,
        "credit": _tidy(credit),
        "page": safe_url(p.get("link")),
    }


def apod_page(day: date) -> str:
    return f"https://apod.nasa.gov/apod/ap{day:%y%m%d}.html"


def random_apod_day(latest: date, rng: random.Random | None = None) -> date:
    return APOD_FIRST + timedelta(days=(rng or random).randrange((latest - APOD_FIRST).days + 1))


# ───────────────────────────── API clients ─────────────────────────────

def get_json(url: str, params: dict | None = None) -> Any:
    return _get_json(url, params, service="NASA's image service")


@st.cache_data(ttl=3600, show_spinner=False)
def search_images(query: str, page: int = 1, year_start: int | None = None, year_end: int | None = None,
                  center: str | None = None, page_size: int = PAGE_SIZE) -> dict:
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
    params: dict[str, Any] = {"q": query[:MAX_QUERY_CHARS], "media_type": "image", "page": page, "page_size": page_size}
    if year_start:
        params["year_start"] = str(year_start)
    if year_end:
        params["year_end"] = str(year_end)
    if center:
        params["center"] = center
    data = get_json(f"{API}/search", params)
    col = data.get("collection") if isinstance(data, dict) else None
    if not isinstance(col, dict) or not isinstance(col.get("items", []), list):
        raise ApiError("NASA's image service returned an unexpected search response.")
    items = [p for p in (parse_item(i) for i in col.get("items", [])) if p]
    try:
        metadata = col.get("metadata") if isinstance(col.get("metadata"), dict) else {}
        total = max(0, int(metadata.get("total_hits", len(items))))
    except (OverflowError, TypeError, ValueError):
        total = len(items)
    return {"items": items, "total": total}


@st.cache_data(ttl=3600, show_spinner=False)
def get_item(nasa_id: str) -> dict | None:
    data = get_json(f"{API}/search", {"nasa_id": nasa_id, "media_type": "image"})
    col = data.get("collection") if isinstance(data, dict) else None
    if not isinstance(col, dict) or not isinstance(col.get("items", []), list):
        raise ApiError("NASA's image service returned an unexpected detail response.")
    for raw in col.get("items", []):
        parsed = parse_item(raw)
        if parsed:
            return parsed
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def get_assets(nasa_id: str) -> dict[str, str]:
    return classify_assets(get_json(f"{API}/asset/{quote(str(nasa_id), safe='')}"))


def nasa_api_key() -> str:
    """NASA_API_KEY from .streamlit/secrets.toml or the environment; NASA's shared DEMO_KEY otherwise."""
    try:
        key = st.secrets.get("NASA_API_KEY", "")
    except FileNotFoundError:                      # no secrets file at all
        key = ""
    return str(key or os.environ.get("NASA_API_KEY") or "DEMO_KEY")


def _apod_from_api(day: str) -> dict:
    params = {"api_key": nasa_api_key(), "thumbs": "true"}
    if day:
        params["date"] = day
    item = parse_apod(_get_json(APOD_URL, params, service="NASA's APOD API"))
    if item is None:
        raise ApiError("NASA's APOD API returned an unexpected response.")
    return item


def _apod_from_nasa_science(day: str) -> dict:
    params = {"search": "APOD", "per_page": 5, "_fields": "date,title,content,link"}
    if day:
        d = date.fromisoformat(day)
        params.update(after=f"{d - timedelta(days=1)}T23:59:59", before=f"{day}T23:59:59")
    data = _get_json(APOD_WP_URL, params, service="NASA Science")
    if not isinstance(data, list):
        raise ApiError("NASA Science returned an unexpected response.")
    for post in data:
        item = parse_apod_post(post)
        if item and (not day or item["date"].isoformat() == day):
            return item
    raise ApiError(f"NASA has no Picture of the Day for {day}." if day else "NASA Science returned no recent pictures.")


@st.cache_data(ttl=3600, show_spinner=False)
def get_apod(day: str = "") -> dict:
    """Astronomy Picture of the Day for an ISO date ('' = the most recent one). Uses the APOD API when a personal
    NASA_API_KEY is set, and NASA Science's open feed otherwise (or if the API fails)."""
    if nasa_api_key() != "DEMO_KEY":
        try:
            return _apod_from_api(day)
        except ApiError:
            pass
    return _apod_from_nasa_science(day)


# ───────────────────────────── State and callbacks ─────────────────────────────

ss = st.session_state


def normalize_query(value: Any) -> str:
    """Collapse pasted whitespace and keep URL-supplied searches to a reasonable size."""
    query = re.sub(r"\s+", " ", str(value or "")).strip()
    return query[:MAX_QUERY_CHARS]


def _query_int(qp: Any, key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(qp.get(key, default))
    except (TypeError, ValueError):
        return default
    return min(high, max(low, value))


def init_state() -> None:
    qp = st.query_params
    initial_query = normalize_query(qp.get("q") or DEFAULT_QUERY)
    nasa_id = str(qp.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", nasa_id):
        nasa_id = ""
    year_start = _query_int(qp, "from", MIN_YEAR, MIN_YEAR, THIS_YEAR)
    year_end = _query_int(qp, "to", THIS_YEAR, MIN_YEAR, THIS_YEAR)
    if year_start > year_end:
        year_start, year_end = MIN_YEAR, THIS_YEAR
    center = str(qp.get("center") or "Any")
    if center not in {"Any", *CENTERS}:
        center = "Any"
    sort = str(qp.get("sort") or SORTS[0])
    if sort not in SORTS:
        sort = SORTS[0]
    ss.setdefault("query", initial_query)
    ss.setdefault("selected", nasa_id or None)
    ss.setdefault("page", _query_int(qp, "page", 1, 1, MAX_HITS // PAGE_SIZE + 1))
    ss.setdefault("years", (year_start, year_end))
    ss.setdefault("center", center)
    ss.setdefault("sort", sort)
    ss.setdefault("view", VIEWS[1] if qp.get("view") == "apod" else VIEWS[0])
    try:
        ss.setdefault("_apod_url_date", date.fromisoformat(str(qp.get("date") or "")))
    except ValueError:
        ss.setdefault("_apod_url_date", None)


def reset_view() -> None:
    ss.page, ss.selected = 1, None


def reset_page() -> None:
    ss.page = 1


def reset_filters() -> None:
    ss.years = (MIN_YEAR, THIS_YEAR)
    ss.center = "Any"
    ss.page = 1


def search_for(q: str) -> None:
    ss.query = normalize_query(q)
    reset_view()


def surprise() -> None:
    search_for(random.choice([t for t in TOPICS if t != ss.get("query")]))


def open_detail(nasa_id: str, item: dict | None = None) -> None:
    ss.selected = nasa_id
    if item:
        ss["_selected_item"] = item


def close_detail() -> None:
    ss.selected = None


def go_page(p: int) -> None:
    ss.page = p


def apod_step(days: int) -> None:
    ss.apod_pick = ss.apod_pick + timedelta(days=days)


def apod_go(day: date) -> None:
    ss.apod_pick = day


def sync_query_params() -> None:
    """Keep search, filters, page, and detail selection shareable in the URL."""
    qp, q, sel = st.query_params, ss.query.strip(), ss.selected
    ys, ye = ss.get("years", (MIN_YEAR, THIS_YEAR))
    center = ss.get("center", "Any")
    sort = ss.get("sort", SORTS[0])
    apod = ss.get("view") == VIEWS[1]
    pick, latest = ss.get("apod_pick"), ss.get("_apod_latest")
    values = {
        "view": "apod" if apod else None,
        "date": pick.isoformat() if apod and pick and pick != latest else None,
        "q": q or None,
        "id": sel or None,
        "page": str(ss.page) if ss.page > 1 else None,
        "from": str(ys) if ys > MIN_YEAR else None,
        "to": str(ye) if ye < THIS_YEAR else None,
        "center": center if center != "Any" else None,
        "sort": sort if sort != SORTS[0] else None,
    }
    for key, value in values.items():
        if value is None:
            if key in qp:
                del qp[key]
        elif qp.get(key) != value:
            qp[key] = value


def current_filters() -> dict:
    ys, ye = ss.get("years", (MIN_YEAR, THIS_YEAR))
    center = ss.get("center", "Any")
    return {"year_start": ys if ys > MIN_YEAR else None,
            "year_end": ye if ye < THIS_YEAR else None,
            "center": None if center == "Any" else center}


# ───────────────────────────── UI ─────────────────────────────

def render_controls() -> None:
    c1, c2, c3, c4 = st.columns([3, 1.5, 1.5, 1])
    c1.slider("Year taken", MIN_YEAR, THIS_YEAR, key="years", on_change=reset_page)
    c2.selectbox("NASA center", ["Any", *CENTERS], key="center", on_change=reset_page,
                 format_func=lambda c: "Any center" if c == "Any" else f"{c} · {CENTERS[c]}")
    c3.selectbox("Sort this page", SORTS, key="sort")
    has_filters = ss.years != (MIN_YEAR, THIS_YEAR) or ss.center != "Any"
    c4.button("Reset filters", key="reset_filters", disabled=not has_filters, on_click=reset_filters)
    with st.container(horizontal=True):
        for i, topic in enumerate(TOPICS):
            st.button(topic, key=f"topic_{i}", on_click=search_for, args=(topic,))
        st.button("Surprise me", icon=":material/shuffle:", key="surprise", on_click=surprise)


def pager(where: str, pages: int) -> None:
    if pages <= 1:
        return
    a, b, c = st.columns([1, 2, 1])
    a.button("← Previous", key=f"prev_{where}", disabled=ss.page <= 1, on_click=go_page, args=(ss.page - 1,))
    b.markdown(f"<div style='text-align:center;padding-top:0.4rem'>Page {ss.page} of {pages}</div>",
               unsafe_allow_html=True)
    c.button("Next →", key=f"next_{where}", disabled=ss.page >= pages, on_click=go_page, args=(ss.page + 1,))


def render_results() -> None:
    query = normalize_query(ss.query)
    if not query:
        st.info("Type something above, or pick a topic.")
        return
    try:
        with st.spinner("Searching NASA's archive…"):
            res = search_images(query, ss.page, **current_filters())
    except ApiError as e:
        st.error(f"Image search failed. {e}")
        return
    items, total = res["items"], res["total"]
    if not items and total:
        st.warning("NASA found matching records, but none included a usable image preview. Try another search.")
        return
    if not items:
        st.info(f"No images found for '{md_escape(query)}' with these filters. Try a broader search or reset the filters.")
        return

    pages = page_count(total)
    if ss.page > pages:
        ss.page = pages
        st.rerun()
    start = (ss.page - 1) * PAGE_SIZE + 1
    note = f" The archive only lets you page through the first {MAX_HITS:,}." if total > MAX_HITS else ""
    safe_query = md_escape(query)
    st.markdown(f"**{total:,}** images match **{safe_query}**. Showing {start}–{start + len(items) - 1}.{note}")

    kws = top_keywords(items, 8, exclude=query)
    if kws:
        st.caption("Explore related topics")
        with st.container(horizontal=True):
            for i, kw in enumerate(kws):
                st.button(kw, key=f"kw_{i}", on_click=search_for, args=(kw,))

    pager("top", pages)
    shown = sort_items(items, ss.get("sort", SORTS[0]))
    for row in range(0, len(shown), 4):
        for k, (col, it) in enumerate(zip(st.columns(4), shown[row:row + 4])):
            with col, st.container(border=True, key=f"card_img_{row + k}"):
                st.image(it["thumb"], width="stretch")
                st.markdown(f"**{md_escape(shorten(it['title'], 60))}**")
                st.caption(" · ".join(x for x in (it["date"][:4], it["center"]) if x) or " ")
                st.button("View details", key=f"open_{it['nasa_id']}", on_click=open_detail,
                          args=(it["nasa_id"], it))
    pager("bottom", pages)


def render_detail(nasa_id: str) -> None:
    st.button("← Back to results", key="back_top", on_click=close_detail)
    try:
        item = get_item(nasa_id)
    except ApiError as e:
        cached = ss.get("_selected_item")
        if not isinstance(cached, dict) or cached.get("nasa_id") != nasa_id:
            st.error(f"Could not load this image. {e}")
            return
        item = cached
        st.warning(f"NASA's image service is unavailable; showing the previously loaded details. {e}")
    if not item:
        st.warning("Couldn't find that image.")
        return
    ss["_selected_item"] = item
    try:
        assets = get_assets(nasa_id)
    except ApiError as e:
        assets = {}
        st.info(f"High-resolution download options are unavailable right now. {e}")

    left, right = st.columns([3, 2])
    with left:
        st.image(pick_display(assets, item["thumb"]), width="stretch")
    with right:
        st.subheader(md_escape(item["title"]))
        age = age_text(item["date"])
        facts = [
            ("Date", f"{item['date']} · {age}" if item["date"] and age else item["date"]),
            ("Center", center_name(item["center"])),
            ("Credit", item["photographer"]),
            ("Location", item["location"]),
            ("NASA ID", item["nasa_id"]),
        ]
        st.markdown(facts_html(facts), unsafe_allow_html=True)
        options = download_options(assets)
        if options:
            st.markdown("#### Download")
            for col, (label, url) in zip(st.columns(len(options)), options):
                col.link_button(label, url)
            st.caption("Originals can be very large, and some are TIFF files.")
        st.link_button("Open on NASA's site", f"https://images.nasa.gov/details/{quote(item['nasa_id'], safe='')}")
        st.caption("The page URL now points to this image, so you can copy it to share.")

    st.markdown("#### About this image")
    description = item["description"] or "No description available."
    st.markdown(md_escape(shorten(description, 6000)))

    if item["keywords"]:
        st.markdown("#### Explore by keyword")
        with st.container(horizontal=True):
            for i, kw in enumerate(item["keywords"][:12]):
                st.button(kw, key=f"dkw_{i}", on_click=search_for, args=(kw,))
        try:
            related = [r for r in search_images(item["keywords"][0], 1, page_size=9)["items"]
                       if r["nasa_id"] != nasa_id][:4]
        except ApiError:
            related = []
        if related:
            st.markdown(f"#### More like this: {md_escape(item['keywords'][0])}")
            for k, (col, r) in enumerate(zip(st.columns(4), related)):
                with col, st.container(border=True, key=f"card_rel_{k}"):
                    st.image(r["thumb"], width="stretch")
                    st.caption(md_escape(shorten(r["title"], 60)))
                    st.button("View", key=f"rel_{r['nasa_id']}", on_click=open_detail,
                              args=(r["nasa_id"], r))


def render_apod() -> None:
    try:
        with st.spinner("Fetching today's picture…"):
            latest = get_apod()
    except ApiError as e:
        st.error(f"Could not load the Astronomy Picture of the Day. {e}")
        st.caption("Pictures load from [NASA Science](https://science.nasa.gov/apod/); check that the site opens in your browser.")
        return
    last = latest["date"]
    ss["_apod_latest"] = last
    if "apod_pick" not in ss:
        ss.apod_pick = ss.get("_apod_url_date") or last
    ss.apod_pick = min(last, max(APOD_FIRST, ss.apod_pick))

    with st.container(horizontal=True, vertical_alignment="bottom"):
        st.button("← Previous day", key="apod_prev", disabled=ss.apod_pick <= APOD_FIRST, on_click=apod_step, args=(-1,))
        st.date_input("Date", key="apod_pick", min_value=APOD_FIRST, max_value=last, width=170)
        st.button("Next day →", key="apod_next", disabled=ss.apod_pick >= last, on_click=apod_step, args=(1,))
        st.button("Latest", key="apod_latest", disabled=ss.apod_pick == last, on_click=apod_go, args=(last,))
        st.button("Random day", icon=":material/shuffle:", key="apod_random", on_click=apod_go, args=(random_apod_day(last),))

    day = ss.apod_pick
    try:
        item = latest if day == last else get_apod(day.isoformat())
    except ApiError as e:
        st.error(f"Could not load the picture for {day:%d %B %Y}. {e}")
        return

    left, right = st.columns([3, 2])
    with left:
        if item["media_type"] == "image" and item["url"]:
            st.image(item["url"], width="stretch")
        elif item["media_type"] == "video" and item["url"]:
            try:
                st.video(item["url"])
            except Exception:                      # an embed type st.video can't play: show its thumbnail instead
                if item["thumb"]:
                    st.image(item["thumb"], width="stretch")
                st.link_button("Watch the video", item["url"])
        elif item["thumb"]:
            st.image(item["thumb"], width="stretch")
        else:
            st.info("This day's entry is interactive or a format that can't be shown here. Open it on NASA's site.")
    with right:
        st.subheader(md_escape(item["title"]))
        age = age_text(day.isoformat())
        st.markdown(facts_html([
            ("Date", f"{day:%d %B %Y}" + (f" · {age}" if age and day != last else " · today's picture" if day == last else "")),
            ("Credit", item["credit"] or "Public domain (NASA)"),
            ("Type", item["media_type"].title()),
        ]), unsafe_allow_html=True)
        with st.container(horizontal=True):
            if item["hdurl"]:
                st.link_button("Full resolution", item["hdurl"])
            st.link_button("Open on NASA's site", item.get("page") or apod_page(day))
        st.markdown(md_escape(item["explanation"] or "No explanation available."))
    st.caption("Astronomy Picture of the Day has been curated by NASA since 16 June 1995; pictures load from its home on "
               "[NASA Science](https://science.nasa.gov/apod/), no API key needed. The page URL points to this date, so you can share it.")


def main() -> None:
    st.set_page_config(page_title="NASA Image Explorer", page_icon="🔭", layout="wide")
    init_state()
    eyebrow("NASA image library")
    st.title("NASA Image Explorer")
    st.caption("Search NASA's public image library, or browse 30 years of the Astronomy Picture of the Day.")
    st.segmented_control("View", VIEWS, key="view", required=True, label_visibility="collapsed", on_change=close_detail)
    if ss.view == VIEWS[1]:
        render_apod()
    else:
        st.text_input("Search", key="query", max_chars=MAX_QUERY_CHARS, on_change=reset_view,
                      label_visibility="collapsed",
                      placeholder="Search NASA's image library: nebula, apollo 11, mars rover…")
        if ss.selected:
            render_detail(ss.selected)
        else:
            render_controls()
            render_results()
    sync_query_params()


if __name__ == "__main__":
    main()
