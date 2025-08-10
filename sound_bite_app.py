# app.py — SongSnap++: Year & Popularity Guess + Supabase Leaderboard
import os, json, random, hashlib, re
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
SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL", ""))
SUPABASE_ANON_KEY = st.secrets.get("SUPABASE_ANON_KEY", os.getenv("SUPABASE_ANON_KEY", ""))
SUPABASE_TABLE = st.secrets.get("SUPABASE_TABLE_YEARPOP", os.getenv("SUPABASE_TABLE_YEARPOP", "songsnap_yearpop"))

CENTRAL = ZoneInfo("America/Chicago")

# ------------------ SUPABASE (optional) ------------------
sb_client = None
if SUPABASE_URL and SUPABASE_ANON_KEY:
    try:
        from supabase import create_client, Client
        sb_client: "Client" = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    except Exception as e:
        st.warning(f"Supabase not initialized: {e}. Leaderboard disabled.")

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
    parts = 0
    score = 1000
    details = []

    if isinstance(year_true, int):
        parts += 1
        dy = abs(year_true - year_guess)
        year_penalty = min(20 * dy, 600)
        score -= year_penalty
        details.append(f"Year off by {dy} → −{year_penalty} pts")
    else:
        details.append("Year unavailable → no penalty")

    if isinstance(pop_true, int):
        parts += 1
        dp = abs(pop_true - pop_guess)
        pop_penalty = min(8 * dp, 600)
        score -= pop_penalty
        details.append(f"Popularity off by {dp} → −{pop_penalty} pts")
    else:
        details.append("Popularity unavailable → no penalty")

    if parts == 0:
        return {"score": 0, "details": ["No ground truth in dataset."], "year_err": None, "pop_err": None, "total_err": None}

    score = max(50, int(score))
    total_err = (abs(year_true - year_guess) if isinstance(year_true, int) else 0) + (abs(pop_true - pop_guess) if isinstance(pop_true, int) else 0)
    return {
        "score": score,
        "details": details,
        "year_err": (abs(year_true - year_guess) if isinstance(year_true, int) else None),
        "pop_err": (abs(pop_true - pop_guess) if isinstance(pop_true, int) else None),
        "total_err": total_err,
    }

def coerce_int(x):
    try:
        v = int(float(x))
        return v
    except Exception:
        return None

# ------------------ DATA LOADER ------------------
@st.cache_data(show_spinner=True)
def load_tracks(url_or_path: str):
    df = pd.read_csv(url_or_path)

    # required for this mode
    if "title" not in df.columns: df["title"] = ""
    if "artist" not in df.columns: df["artist"] = ""
    if "year" not in df.columns: df["year"] = ""
    if "popularity" not in df.columns: df["popularity"] = ""
    if "spotify_url" not in df.columns: df["spotify_url"] = ""

    # helpful extras (don’t break if absent)
    for c in ["facts_json", "choices_json"]:
        if c in df.columns and df[c].dtype == object:
            df[c] = df[c].apply(lambda x: json.loads(x.replace("’","'").replace("—","-")) if isinstance(x, str) else [])

    df["year_num"] = df["year"].apply(coerce_int)
    df["pop_num"]  = df["popularity"].apply(coerce_int)
    df["spotify_url"] = df["spotify_url"].fillna("").astype(str)

    years = [y for y in df["year_num"].tolist() if isinstance(y, int) and 1900 <= y <= 2100]
    yr_min, yr_max = (min(years), max(years)) if years else (1960, datetime.now(CENTRAL).year)

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
username = st.sidebar.text_input("Your name (for leaderboard):", value="", placeholder="e.g., Kathy")
st.sidebar.write(f"Rounds played: **{st.session_state.rounds}**")
st.sidebar.write(f"Total score: **{st.session_state.total_score}**")

# ------------------ PICK SONG ------------------
if st.session_state.mode == "Daily Challenge":
    st.session_state.row = pick_daily_row(tracks)
    date_str = datetime.now(CENTRAL).strftime("%Y-%m-%d")
else:
    date_str = "arcade"
row = st.session_state.row

# ------------------ HEADER ------------------
st.markdown("<h1 style='margin-top:0'>🎶 Guess the Year & Popularity</h1>", unsafe_allow_html=True)
st.write(f"**{row.get('title', '')}** — {row.get('artist','')}")

# ------------------ SPOTIFY PLAYER ------------------
spotify_url = row.get("spotify_url", "")
spotify_embed(spotify_url, height=152)

st.divider()

# ------------------ GUESS UI ------------------
col1, col2 = st.columns(2)
with col1:
    default_year = min(max(2000, YEAR_MIN), YEAR_MAX)
    year_guess = st.slider("Release year", min_value=YEAR_MIN, max_value=YEAR_MAX, value=default_year)
with col2:
    pop_guess = st.slider("Spotify popularity (0–100)", min_value=0, max_value=100, value=70)

submit = st.button("Submit Guess ✅", disabled=st.session_state.locked)

if submit and not st.session_state.locked:
    st.session_state.locked = True
    year_true = row.get("year_num", None)
    pop_true  = row.get("pop_num", None)

    result = score_guess(year_true, pop_true, year_guess, pop_guess)
    st.success(f"Score this round: **{result['score']}**")
    for d in result["details"]:
        st.write("• " + d)

    a_year = (str(year_true) if year_true is not None else "unknown")
    a_pop  = (str(pop_true) if pop_true is not None else "unknown")
    st.info(f"Answer → Year: **{a_year}**, Popularity: **{a_pop}**")

    facts = row.get("facts_json", [])
    if isinstance(facts, list) and facts:
        with st.expander("Pop-culture facts"):
            for f in facts:
                st.write("• " + str(f))

    st.session_state.total_score += result["score"]
    st.session_state.rounds += 1

    # ---- SAVE TO LEADERBOARD (daily mode only + username present) ----
    if sb_client and st.session_state.mode == "Daily Challenge" and username.strip():
        try:
            elapsed_ms = int((datetime.now(CENTRAL).timestamp() - st.session_state.start_time) * 1000)
            payload = {
                "date": date_str,
                "username": username.strip()[:32],
                "score": result["score"],
                "year_err": result["year_err"],
                "pop_err": result["pop_err"],
                "total_abs_error": result["total_err"],
                "elapsed_ms": elapsed_ms,
                "title": row.get("title", ""),
                "artist": row.get("artist", ""),
            }

            # Upsert-like behavior: keep best score; tie-breaker lower total_abs_error, then lower elapsed_ms
            existing = (
                sb_client.table(SUPABASE_TABLE)
                .select("id,score,total_abs_error,elapsed_ms")
                .eq("date", date_str)
                .eq("username", payload["username"])
                .execute()
            )

            def is_better(new, old):
                if new["score"] != old.get("score", -1):
                    return new["score"] > old.get("score", -1)
                if (new.get("total_abs_error") or 10**9) != (old.get("total_abs_error") or 10**9):
                    return (new.get("total_abs_error") or 10**9) < (old.get("total_abs_error") or 10**9)
                return (new.get("elapsed_ms") or 10**9) < (old.get("elapsed_ms") or 10**9)

            if existing.data:
                old = existing.data[0]
                if is_better(payload, old):
                    sb_client.table(SUPABASE_TABLE).update(payload).eq("id", old["id"]).execute()
            else:
                sb_client.table(SUPABASE_TABLE).insert(payload).execute()
        except Exception as e:
            st.caption(f"Leaderboard save failed: {e}")

st.divider()

# ------------------ NEXT ROUND ------------------
def next_round():
    st.session_state.locked = False
    st.session_state.start_time = datetime.now(CENTRAL).timestamp()
    if st.session_state.mode == "Arcade Mix":
        st.session_state.row = new_round_row(tracks)

st.button("Next Song ▶️", on_click=next_round)

# ------------------ LEADERBOARD (daily only) ------------------
if st.session_state.mode == "Daily Challenge":
    st.subheader("🏆 Today’s Leaderboard")
    if sb_client:
        try:
            res = (
                sb_client.table(SUPABASE_TABLE)
                .select("username,score,year_err,pop_err,total_abs_error,elapsed_ms")
                .eq("date", datetime.now(CENTRAL).strftime("%Y-%m-%d"))
                .order("score", desc=True)
                .order("total_abs_error", desc=False)
                .order("elapsed_ms", desc=True)   # show faster lower in case of ties? flip if you prefer
                .limit(25)
                .execute()
            )
            lb = pd.DataFrame(res.data)
            if len(lb) == 0:
                st.caption("Be the first on the board today!")
            else:
                st.dataframe(lb)
        except Exception as e:
            st.caption(f"Leaderboard unavailable: {e}")
    else:
        st.caption("Set SUPABASE_URL and SUPABASE_ANON_KEY to enable the leaderboard.")
