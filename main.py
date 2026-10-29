import streamlit as st
from theme import apply_theme

apply_theme()

pages = [
    st.Page("space_tracker.py", title="Space Tracker", icon="🛰️", default=True),
    st.Page("photo_fetcher.py", title="Photo Fetcher", icon="🌌"),
    st.Page("flight_simulator.py", title="Flight Simulator", icon="🚀"),
]

st.navigation(pages).run()