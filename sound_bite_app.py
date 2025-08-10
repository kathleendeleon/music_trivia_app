# app.py — SongSnap++: Year & Popularity Guess
# Players guess the release year and Spotify popularity for each track.
# Visible Spotify embed player; dataset stays the same (uses spotify_url, year, popularity).

import os, json, random, hashlib, re, math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ------------------ PAGE CONFIG ------------------
st.set_page_config(page_title="SongSnap++: Year & Popularity", page_icon="📻", layout="centered")

# ------------------ SETTINGS / SECRETS ------------------
DATASET_URL = st.secrets.get(
    "DATASET_URL",
    os.getenv("DATASET_URL", "https://raw.githubusercontent.com/kathleendeleon/music_trivia_app/refs/heads/main/songsnap_from_spotify3.csv")
)

CENTRAL = ZoneInfo("America/Chicago")

# ------------------ HELPERS ------------------
def today_seed() -> int:
    d = datetime.now(CENTRAL).strftime("%Y-%m-%d")
    return int(hashlib.sha256(d.encode()).hexdigest(), 16)

def pick_daily_row(df: pd.DataFrame) -> pd.Series:
    rng = random.Random(today_seed())
    return df.iloc[rng.randrange(len(df))]

def new_round_row(df: pd.DataFrame) -> pd.Series:
    return df.sample(1, random_state=random.randint(0, 10_000)).iloc[0]

def extract_spotify_track_id(spotify_url: str | None) -> str | None:
    if not isinstance(spotify_url, str):
        return None
    m = re.search(r"spotify\.com/track/([A-Za-z0-9]+)", spotify_url)
    if m: return m.group(1)
    m = re.search(r"spotify:track:([A-Za-z0-9]+)", spotify_url)
    return m.group(1) if m else None

def spotify_embed(track_url: str, height: int = 152):
    """Visible Spotify embed player (title/artist show)."""
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

def score_guess(year_true: int | None, pop_true: int | None, year_guess: int, pop_guess: int) -> dict:
    # If data missing, skip that part from scoring
    parts = 0
    score = 1000
    details = []

    if isinstance(year_true, int):
        parts += 1
        dy = abs(year_true - year_guess)
        # subtract 20 points per year off; cap how brutal it gets
        year_penalty = min(20 * dy, 600)
        score -= year_penalty
        details.append(f"Year off by {dy} → −{year_penalty} pts")
    else:
        details.append("Year unavailable → no penalty")

    if isinstance(pop_true, int):
        parts += 1
        dp = abs(pop_true - pop_guess)
        # subtract 8 points per popularity point off; cap
        pop_penalty = min(8 * dp, 600)
        score -= pop_penalty
        details.append(f"Popularity off by {dp} → −{pop_penalty} pts")
    else:
        details.append("Popularity unavailable → no penalty")

    if parts == 0:
        # should not happen with a sane dataset
        return {"score": 0, "details": ["No ground truth in dataset."], "year_err": None, "pop_err": None}

    score = max(50, int(score))
    return {
        "score": score,
        "details": details,
        "year_err": (abs(year_true - year_guess) if isinstance(year_true, int) else None),
        "pop_err": (abs(pop_true - pop_guess) if isinstance(pop_true, int) else None),
    }

# ------------------ DATA LOADER ------------------
@st.cache_data(show_spinner=True)
def load_tracks(url_or_path: str) -> pd.DataFrame:
    df = pd.read_csv(url_or_path)

    # Expected columns from your builder/app
    expected = [
        "id","title","artist","emoji","choices_json","answer_idx",
        "preview_1s_url","preview_3s_url","preview_5s_url","context_hint",
        "facts_json","pack","year","tv_movie_ref"
    ]
    missing = [c for c in expected if c not in df.columns]
    # It’s okay if some are missing (we won’t use choices/audio here), but "title","artist","year","spotify_url","popularity" matter most.
    # Create safe fallbacks and coerce types.
    if "title" not in df.columns: df["title"] = ""
    if "artist" not in df.columns: df["artist"] = ""
    if "year" not in df.columns: df["year"] = ""
    if "spotify_url" not in df.columns: df["spotify_url"] = ""
    if "popularity" not in df.columns: df["popularity"] = ""

    # JSON-ish columns: keep from crashing if present
    for c in ["facts_json", "choices_json"]:
        if c in df.columns:
            df[c] = df[c].apply(lambda x: json.loads(x.replace("’","'").replace("—","-")) if isinstance(x, str) else [])

    # Coerce numeric fields
    def coerce_int(x):
        try:
            # some CSVs hold empty string -> NaN -> float; convert safely
            v = int(float(x))
            return v
        except Exception:
            return None

    df["year_num"] = df["year"].apply(coerce_int)
    df["pop_num"]  = df["popularity"].apply(coerce_int)

    # Stringify URLs
    for c in ["spotify_url"]:
        df[c] = df[c].fillna("").astype(str)

    # Build min/max year range for the slider
    years = [y for y in df["year_num"].tolist() if isinstance(y, int) and 1900 <= y <= 2100]
    if years:
        yr_min, yr_max = min(years), max(years)
    else:
        yr_min, yr_max = 1960, datetime.now(CENTRAL).year

    return df, yr_min, yr_max

tracks, YEAR_MIN, YEAR_MAX = load_tracks(DATASET_URL)
if len(tracks) == 0:
    st.error("No tracks found in dataset.")
    st.stop()

# ------------------ STATE ------------------
if "mode" not in st.session_state:
    st.session_state.mode = "Daily Challenge"
if "row" not in st.session_state:
    st.session_state.row = pick_daily_row(tracks)
if "locked" not in st.session_state:
    st.session_state.locked = False
if "start_time" not in st.session_state:
    st.session_state.start_time = datetime.now(CENTRAL).timestamp()
if "total_score" not in st.session_state:
    st.session_state.total_score = 0
if "rounds" not in st.session_state:
    st.session_state.rounds = 0

# ------------------ SIDEBAR ------------------
st.sidebar.title("📻 SongSnap++ — Year & Popularity")
st.sidebar.caption("Guess the release year and Spotify popularity.")
st.session_state.mode = st.sidebar.radio("Mode", ["Daily Challenge", "Arcade Mix"], index=0)
st.sidebar.write(f"Rounds played: **{st.session_state.rounds}**")
st.sidebar.write(f"Total score: **{st.session_state.total_score}**")

# Choose row
if st.session_state.mode == "Daily Challenge":
    st.session_state.row = pick_daily_row(tracks)
    date_str = datetime.now(CENTRAL).strftime("%Y-%m-%d")
else:
    # keep current row unless user clicks Next
    date_str = "arcade"

row = st.session_state.row

# ------------------ HEADER ------------------
st.markdown("<h1 style='margin-top:0'>🎶 Guess the Year & Popularity</h1>", unsafe_allow_html=True)
st.write(f"**{row.get('title', '')}** — {row.get('artist','')}")

# ------------------ PLAYER (visible embed) ------------------
spotify_url = row.get("spotify_url", "")
spotify_embed(spotify_url, height=152)

st.divider()

# ------------------ GUESS UI ------------------
col1, col2 = st.columns(2)
with col1:
    year_guess = st.slider("Release year", min_value=YEAR_MIN, max_value=YEAR_MAX, value=min(max(2000, YEAR_MIN), YEAR_MAX))
with col2:
    pop_guess = st.slider("Spotify popularity (0–100)", min_value=0, max_value=100, value=70)

guess_btn = st.button("Submit Guess ✅", disabled=st.session_state.locked)

if guess_btn and not st.session_state.locked:
    st.session_state.locked = True
    # Ground truth
    year_true = row.get("year_num", None)
    pop_true  = row.get("pop_num", None)

    result = score_guess(year_true, pop_true, year_guess, pop_guess)

    st.success(f"Score this round: **{result['score']}**")
    for d in result["details"]:
        st.write("• " + d)

    # Reveal answers
    a_year = (str(year_true) if year_true is not None else "unknown")
    a_pop  = (str(pop_true) if pop_true is not None else "unknown")
    st.info(f"Answer → Year: **{a_year}**, Popularity: **{a_pop}**")

    # Facts, if available
    facts = row.get("facts_json", [])
    if isinstance(facts, list) and facts:
        with st.expander("Pop-culture facts"):
            for f in facts:
                st.write("• " + str(f))

    st.session_state.total_score += result["score"]
    st.session_state.rounds += 1

st.divider()

# ------------------ NEXT ROUND ------------------
def next_round():
    st.session_state.locked = False
    st.session_state.start_time = datetime.now(CENTRAL).timestamp()
    if st.session_state.mode == "Arcade Mix":
        st.session_state.row = new_round_row(tracks)

st.button("Next Song ▶️", on_click=next_round)

# ------------------ FOOTER ------------------
with st.expander("Dataset columns I use"):
    st.write("• title, artist, spotify_url, year (→ year_num), popularity (→ pop_num)")
    st.write("• facts_json (optional)")

