# app.py — SongSnap++: Guess the Year (10-round session)
# - Uses your Spotify CSV (expects at least: title, artist, year, spotify_url)
# - Visible Spotify embed per track
# - Guess the release year, 10 rounds, session scoreboard at the end

import os, json, random, hashlib, re
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ------------------ PAGE CONFIG ------------------
st.set_page_config(page_title="SongSnap++: Guess the Year", page_icon="📻", layout="centered")

# ------------------ SETTINGS / SECRETS ------------------
DATASET_URL = st.secrets.get(
    "DATASET_URL",
    os.getenv("DATASET_URL", "https://raw.githubusercontent.com/kathleendeleon/music_trivia_app/refs/heads/main/songsnap_from_spotify3.csv")
)
ROUNDS_TARGET = 10
CENTRAL = ZoneInfo("America/Chicago")

# ------------------ HELPERS ------------------
def extract_spotify_track_id(spotify_url: str | None) -> str | None:
    if not isinstance(spotify_url, str):
        return None
    m = re.search(r"spotify\.com/track/([A-Za-z0-9]+)", spotify_url)
    if m: return m.group(1)
    m = re.search(r"spotify:track:([A-Za-z0-9]+)", spotify_url)
    return m.group(1) if m else None

def spotify_embed(track_url: str, height: int = 152):
    tid = extract_spotify_track_id(track_url)
    if not tid:
        st.caption("No Spotify link available for this track.")
        return
    components.iframe(
        f"https://open.spotify.com/embed/track/{tid}?utm_source=generator",
        height=height,
        width=700,
        scrolling=False,
    )

def to_int_or_none(x):
    try:
        if x is None:
            return None
        if isinstance(x, str):
            x = x.strip()
            if x == "":
                return None
        return int(float(x))
    except Exception:
        return None

def score_year(year_true, year_guess):
    def to_int_or_none(x):
        try:
            if x is None: return None
            if isinstance(x, str):
                x = x.strip()
                if x == "": return None
            return int(float(x))
        except Exception:
            return None

    yt = to_int_or_none(year_true)
    yg = to_int_or_none(year_guess)

    if yt is None or yg is None:
        # Don’t punish players if data is missing
        return 500, 0

    dy = abs(yt - yg)
    score = max(50, 500 - 50 * dy)
    return score, dy


def pick_new_index(df_len: int, used: set[int]) -> int:
    choices = [i for i in range(df_len) if i not in used]
    if not choices:
        # If we somehow run out (dataset < rounds), allow repeats but shuffle
        return random.randrange(df_len)
    return random.choice(choices)

# ------------------ DATA LOADER ------------------
@st.cache_data(show_spinner=True)
def load_tracks(url_or_path: str):
    df = pd.read_csv(url_or_path)

    # Ensure fields exist
    for c in ["title", "artist", "year", "spotify_url"]:
        if c not in df.columns:
            df[c] = ""

    # Coerce
    df["year_num"] = df["year"].apply(to_int_or_none)
    df["spotify_url"] = df["spotify_url"].fillna("").astype(str)

    # Optional JSON fields (won't break if missing)
    for c in ["facts_json"]:
        if c in df.columns and df[c].dtype == object:
            df[c] = df[c].apply(lambda x: json.loads(x.replace("’","'").replace("—","-")) if isinstance(x, str) else [])

    # Year slider bounds
    years = [y for y in df["year_num"].tolist() if isinstance(y, int) and 1900 <= y <= 2100]
    yr_min, yr_max = (min(years), max(years)) if years else (1960, datetime.now(CENTRAL).year)
    return df, yr_min, yr_max

tracks, YEAR_MIN, YEAR_MAX = load_tracks(DATASET_URL)
if len(tracks) == 0:
    st.error("No tracks found in dataset.")
    st.stop()

# ------------------ STATE ------------------
if "round_num" not in st.session_state:
    st.session_state.round_num = 1
if "used_idx" not in st.session_state:
    st.session_state.used_idx = set()
if "cur_idx" not in st.session_state:
    st.session_state.cur_idx = pick_new_index(len(tracks), st.session_state.used_idx)
if "locked" not in st.session_state:
    st.session_state.locked = False
if "total_score" not in st.session_state:
    st.session_state.total_score = 0
if "history" not in st.session_state:
    st.session_state.history = []  # list of dicts per round

# ------------------ HEADER ------------------
st.markdown("<h1 style='margin-top:0'>🎶 Guess the Year</h1>", unsafe_allow_html=True)
st.caption(f"Round {st.session_state.round_num} of {ROUNDS_TARGET} • Total score: **{st.session_state.total_score}**")

row = tracks.iloc[st.session_state.cur_idx]

# ------------------ CURRENT SONG ------------------
st.subheader("Now Playing")
st.write(f"**{row.get('title','')}** — {row.get('artist','')}")
spotify_embed(row.get("spotify_url", ""), height=152)

st.divider()

# ------------------ GUESS UI ------------------
default_year = min(max(2000, YEAR_MIN), YEAR_MAX)
year_guess = st.slider("Your guess: release year", min_value=YEAR_MIN, max_value=YEAR_MAX, value=default_year)

submit = st.button("Submit Guess ✅", type="primary", disabled=st.session_state.locked)

if submit and not st.session_state.locked:
    st.session_state.locked = True
    year_true = row.get("year_num", None)
    score, err = score_year(year_true, year_guess)
    st.session_state.total_score += score

    # Reveal
    a_year = (str(year_true) if year_true is not None else "unknown")
    st.success(f"Score this round: **{score}**  •  Off by **{err}** year(s)")
    st.info(f"Answer → Year: **{a_year}**")

    # Optional facts
    facts = row.get("facts_json", [])
    if isinstance(facts, list) and facts:
        with st.expander("Pop-culture facts"):
            for f in facts:
                st.write("• " + str(f))

    # Save history row
    st.session_state.history.append({
        "round": st.session_state.round_num,
        "title": row.get("title",""),
        "artist": row.get("artist",""),
        "your_guess": str(year_guess),
        "answer_year": str(year_true),
        "error_years": int(err),
        "score": int(score),
    })

st.divider()

# ------------------ NEXT / RESULTS ------------------
def go_next_round():
    st.session_state.round_num += 1
    st.session_state.locked = False
    st.session_state.used_idx.add(st.session_state.cur_idx)
    st.session_state.cur_idx = pick_new_index(len(tracks), st.session_state.used_idx)

# If we’ve finished all rounds, show results
if st.session_state.round_num > ROUNDS_TARGET:
    st.subheader("🏁 Session Results")
    if len(st.session_state.history):
        dfh = pd.DataFrame(st.session_state.history)
        st.dataframe(dfh, use_container_width=True, hide_index=True)
        max_score = len(dfh) * 500  # 500 max points per round
        total_score = dfh["score"].sum()
        percent_score = round((total_score / max_score) * 100, 1)
    st.markdown(f"### 🏁 Final Score: **{total_score} / {max_score}**  ({percent_score}%)")
    #st.success(f"Final score: **{st.session_state.total_score}**")

    if st.button("Play Again 🔁"):
        # Reset session
        st.session_state.round_num = 1
        st.session_state.used_idx = set()
        st.session_state.cur_idx = pick_new_index(len(tracks), st.session_state.used_idx)
        st.session_state.locked = False
        st.session_state.total_score = 0
        st.session_state.history = []
        st.rerun()
else:
    # Only allow Next after submitting
    st.button("Next Song ▶️", on_click=go_next_round, disabled=not st.session_state.locked)

