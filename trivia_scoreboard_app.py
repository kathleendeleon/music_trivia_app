# app.py — SongSnap++: Guess the Year (10-round session) + Supabase Leaderboard
# - Uses CSV with at least: title, artist, year, spotify_url
# - Records finished sessions to Supabase and shows a global leaderboard

import os, json, random, re, time
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
    os.getenv("DATASET_URL", "https://raw.githubusercontent.com/kathleendeleon/music_trivia_app/refs/heads/main/songsnap_from_spotify4.csv")
)
ROUNDS_TARGET = 10
CENTRAL = ZoneInfo("America/Chicago")

SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL", ""))
SUPABASE_ANON_KEY = st.secrets.get("SUPABASE_ANON_KEY", os.getenv("SUPABASE_ANON_KEY", ""))
SUPABASE_TABLE = st.secrets.get("SUPABASE_TABLE_SESSIONS", os.getenv("SUPABASE_TABLE_SESSIONS", "songsnap_year_sessions"))

# ------------------ SUPABASE (optional) ------------------
sb_client = None
if SUPABASE_URL and SUPABASE_ANON_KEY:
    try:
        from supabase import create_client, Client
        sb_client: "Client" = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    except Exception as e:
        st.warning(f"Supabase not initialized: {e}. Leaderboard disabled.")

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
        width=300,
        scrolling=False,
    )

def to_int_or_none(x):
    try:
        if x is None: return None
        if isinstance(x, str):
            x = x.strip()
            if x == "": return None
        return int(float(x))
    except Exception:
        return None

def score_year(year_true, year_guess):
    # Coerce to plain ints and score
    yt = to_int_or_none(year_true)
    yg = to_int_or_none(year_guess)
    if yt is None or yg is None:
        return 500, 0
    dy = abs(yt - yg)
    score = max(0, 500 - 50 * dy)  # 500 max, -50 per year off, floor 0
    return score, dy

def pick_new_index(df_len: int, used: set[int]) -> int:
    choices = [i for i in range(df_len) if i not in used]
    if not choices:
        return random.randrange(df_len)
    return random.choice(choices)

def submit_session_to_supabase(username: str, total_score: int, max_score: int, rounds: int, elapsed_ms: int, finished_at_iso: str):
    if not sb_client or not username.strip():
        return
    percent = round((total_score / max_score) * 100, 1) if max_score > 0 else 0.0
    payload = {
        "username": username.strip()[:32],
        "total_score": int(total_score),
        "max_score": int(max_score),
        "percent": float(percent),
        "rounds": int(rounds),
        "elapsed_ms": int(elapsed_ms),
        "finished_at": finished_at_iso
    }
    try:
        sb_client.table(SUPABASE_TABLE).insert(payload).execute()
    except Exception as e:
        st.caption(f"(Could not save session to leaderboard: {e})")

def fetch_top_sessions(limit: int = 25) -> pd.DataFrame:
    if not sb_client:
        return pd.DataFrame()
    try:
        res = (
            sb_client.table(SUPABASE_TABLE)
            .select("username,total_score,max_score,percent,rounds,elapsed_ms,finished_at")
            .order("total_score", desc=True)
            .order("percent", desc=True)
            .order("elapsed_ms", desc=True)  # longer elapsed lower in list; flip if you prefer
            .limit(limit)
            .execute()
        )
        return pd.DataFrame(res.data)
    except Exception as e:
        st.caption(f"(Leaderboard fetch failed: {e})")
        return pd.DataFrame()

# ------------------ DATA LOADER ------------------
@st.cache_data(show_spinner=True)
def load_tracks(url_or_path: str):
    df = pd.read_csv(url_or_path)

    for c in ["title", "artist", "year", "spotify_url"]:
        if c not in df.columns:
            df[c] = ""

    df["year_num"] = df["year"].apply(to_int_or_none)
    df["spotify_url"] = df["spotify_url"].fillna("").astype(str)

    if "facts_json" in df.columns and df["facts_json"].dtype == object:
        df["facts_json"] = df["facts_json"].apply(lambda x: json.loads(x.replace("’","'").replace("—","-")) if isinstance(x, str) else [])

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
    st.session_state.history = []  # rows per round
if "session_start" not in st.session_state:
    st.session_state.session_start = time.time()
if "saved_session" not in st.session_state:
    st.session_state.saved_session = False  # prevent duplicate writes

# ------------------ SIDEBAR ------------------
st.sidebar.title("📻 SongSnap++ — Guess the Year")
st.sidebar.caption("10 rounds. Highest total score wins!")
username = st.sidebar.text_input("Your name (for leaderboard):", value="", placeholder="e.g., Kathy")
st.sidebar.write(f"Round: **{st.session_state.round_num}/{ROUNDS_TARGET}**")
st.sidebar.write(f"Total score: **{st.session_state.total_score}**")

# ------------------ HEADER ------------------
st.markdown("<h1 style='margin-top:0'>🎶 Guess the Year</h1>", unsafe_allow_html=True)

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
    year_true_raw = row.get("year_num", None)
    score, err = score_year(year_true_raw, year_guess)
    st.session_state.total_score += score

    # For display
    try:
        a_year = str(int(float(year_true_raw)))
    except Exception:
        a_year = "unknown"

    st.success(f"Score this round: **{score}**  •  Off by **{err}** year(s)")
    st.info(f"Answer → Year: **{a_year}**")

    facts = row.get("facts_json", [])
    if isinstance(facts, list) and facts:
        with st.expander("Pop-culture facts"):
            for f in facts:
                st.write("• " + str(f))

    st.session_state.history.append({
        "round": st.session_state.round_num,
        "title": row.get("title",""),
        "artist": row.get("artist",""),
        "your_guess": str(year_guess),
        "answer_year": str(year_true_raw),
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

# End of game?
if st.session_state.round_num > ROUNDS_TARGET:
    st.subheader("🏁 Session Results")
    if len(st.session_state.history):
        final_df = pd.DataFrame(st.session_state.history)
        st.dataframe(final_df, use_container_width=True, hide_index=True)

        max_score = ROUNDS_TARGET * 500
        total_score = int(final_df["score"].sum())
        percent_score = round((total_score / max_score) * 100, 1)
        st.markdown(f"### 🏁 Final Score: **{total_score} / {max_score}**  ({percent_score}%)")

        # One-time write to Supabase
        if not st.session_state.saved_session:
            elapsed_ms = int((time.time() - st.session_state.session_start) * 1000)
            finished_at_iso = datetime.now(CENTRAL).isoformat()
            submit_session_to_supabase(username, total_score, max_score, ROUNDS_TARGET, elapsed_ms, finished_at_iso)
            st.session_state.saved_session = True

        # Global leaderboard
        st.subheader("🌐 Leaderboard (All-time Top 25)")
        lb = fetch_top_sessions(limit=25)
        if lb.empty:
            st.caption("No scores yet—be the first!")
        else:
            # Friendly formatting
            lb2 = lb.copy()
            lb2["percent"] = lb2["percent"].map(lambda p: f"{p:.1f}%")
            lb2["elapsed_ms"] = lb2["elapsed_ms"].map(lambda ms: f"{ms/1000:.1f}s")
            lb2.rename(columns={
                "username": "Player",
                "total_score": "Score",
                "max_score": "Max",
                "percent": "Percent",
                "rounds": "Rounds",
                "elapsed_ms": "Time",
                "finished_at": "Finished"
            }, inplace=True)
            st.dataframe(lb2, use_container_width=True, hide_index=True)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Play Again 🔁"):
            st.session_state.round_num = 1
            st.session_state.used_idx = set()
            st.session_state.cur_idx = pick_new_index(len(tracks), st.session_state.used_idx)
            st.session_state.locked = False
            st.session_state.total_score = 0
            st.session_state.history = []
            st.session_state.session_start = time.time()
            st.session_state.saved_session = False
            st.rerun()
    with c2:
        st.caption("Tip: Enter your name in the left sidebar before playing to appear on the leaderboard.")
else:
    st.button("Next Song ▶️", on_click=go_next_round, disabled=not st.session_state.locked)
