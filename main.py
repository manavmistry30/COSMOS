import streamlit as st
from theme import apply_theme

apply_theme()

pages = [
    st.Page("space_tracker.py", title="Space Tracker", icon="🛰️", default=True),
    st.Page("photo_fetcher.py", title="Photo Fetcher", icon="🔭"),
    st.Page("space_weather.py", title="Space Weather", icon="🌌"),
]

st.navigation(pages).run()