# app.py — SongSnap++ (clean version with post-round Spotify embed)
import os, time, json, random, hashlib, re
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

# ------------------ CONFIG ------------------
st.set_page_config(page_title="SongSnap++", page_icon="🎵", layout="centered")

# ------------------ SECRETS / ENVS ------------------
SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL", ""))
SUPABASE_ANON_KEY = st.secrets.get("SUPABASE_ANON_KEY", os.getenv("SUPABASE_ANON_KEY", ""))
SUPABASE_TABLE = st.secrets.get("SUPABASE_TABLE", os.getenv("SUPABASE_TABLE", "songsnap_scores"))

# IMPORTANT: Use the RAW GitHub URL (or any http/https/local path)
DATASET_URL = st.secrets.get(
    "DATASET_URL",
    os.getenv("DATASET_URL", "https://raw.githubusercontent.com/kathleendeleon/music_trivia_app/refs/heads/main/songsnap_from_spotify3.csv")
)

# ------------------ SUPABASE (optional) ------------------
sb_client = None
if SUPABASE_URL and SUPABASE_ANON_KEY:
    try:
        from supabase import create_client, Client
        sb_client: "Client" = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    except Exception as e:
        st.warning(f"Supabase not initialized: {e}. Leaderboard disabled.")

# ------------------ HELPERS ------------------
CENTRAL = ZoneInfo("America/Chicago")

def today_seed() -> int:
    d = datetime.now(CENTRAL).strftime("%Y-%m-%d")
    return int(hashlib.sha256(d.encode()).hexdigest(), 16)

def pick_daily_row(df: pd.DataFrame) -> pd.Series:
    rng = random.Random(today_seed())
    return df.iloc[rng.randrange(len(df))]

def new_round_row(df: pd.DataFrame) -> pd.Series:
    return df.sample(1, random_state=random.randint(0, 10_000)).iloc[0]

def score_round(correct: bool, reveal_level: int, start_time: float) -> int:
    elapsed = time.time() - start_time
    base = 1000
    penalty_reveal = 150 * reveal_level
    penalty_time = int(50 * (elapsed // 5))  # -50 per 5s chunk
    emoji_perfect = 200 if reveal_level == 0 else 0
    return 50 if not correct else max(50, base - penalty_reveal - penalty_time + emoji_perfect)

def first_available_preview(row) -> str:
    for k in ("preview_1s_url", "preview_3s_url", "preview_5s_url"):
        v = row.get(k, "")
        if isinstance(v, str) and v.strip():
            return v
    return ""

def extract_spotify_track_id(spotify_url: str | None) -> str | None:
    if not spotify_url or not isinstance(spotify_url, str):
        return None
    m = re.search(r"spotify\.com/track/([A-Za-z0-9]+)", spotify_url)
    if m: return m.group(1)
    m = re.search(r"spotify:track:([A-Za-z0-9]+)", spotify_url)
    return m.group(1) if m else None

def spotify_embed_html(track_id: str, height: int = 152) -> str:
    src = f"https://open.spotify.com/embed/track/{track_id}?utm_source=generator"
    return f"""<iframe style="border-radius:12px" src="{src}" width="100%" height="{height}"
      frameborder="0" allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture"
      loading="lazy"></iframe>"""

def submit_score(date_str: str, username: str, score: int, streak: int, guesses: int, reveal_level: int, elapsed_ms: int):
    if not sb_client:
        return
    payload = {
        "date": date_str,
        "username": username.strip()[:32],
        "score": score,
        "streak": streak,
        "guesses": guesses,
        "reveal_level": reveal_level,
        "elapsed_ms": elapsed_ms,
    }
    try:
        # simple upsert-by-hand (read then update/insert)
        existing = sb_client.table(SUPABASE_TABLE).select("*").eq("date", date_str).eq("username", payload["username"]).execute()
        if existing.data:
            best = max(existing.data[0].get("score", 0), score)
            payload["score"] = best
            sb_client.table(SUPABASE_TABLE).update(payload).eq("date", date_str).eq("username", payload["username"]).execute()
        else:
            sb_client.table(SUPABASE_TABLE).insert(payload).execute()
    except Exception as e:
        st.toast(f"Could not submit score: {e}", icon="⚠️")

def fetch_leaderboard(date_str: str, limit: int = 25) -> pd.DataFrame:
    if not sb_client:
        return pd.DataFrame()
    try:
        res = (
            sb_client.table(SUPABASE_TABLE)
            .select("username,score,streak,guesses,reveal_level,elapsed_ms")
            .eq("date", date_str)
            .order("score", desc=True)
            .order("elapsed_ms", desc=False)
            .limit(limit)
            .execute()
        )
        return pd.DataFrame(res.data)
    except Exception as e:
        st.caption(f"Leaderboard unavailable: {e}")
        return pd.DataFrame()

# ------------------ DATA LOADER ------------------
@st.cache_data(show_spinner=True)
def load_tracks(url_or_path: str) -> pd.DataFrame:
    df = pd.read_csv(url_or_path)

    # required columns
    required = [
        "id","title","artist","emoji","choices_json","answer_idx",
        "preview_1s_url","preview_3s_url","preview_5s_url","context_hint",
        "facts_json","pack","year","tv_movie_ref"
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f"Dataset missing required columns: {missing}")
        st.stop()

    # optional column: spotify_url (for post-round embed)
    if "spotify_url" not in df.columns:
        df["spotify_url"] = ""

    # coerce JSON-like columns robustly
    def _coerce(series: pd.Series):
        out = []
        for x in series:
            if isinstance(x, list):
                out.append(x); continue
            if not isinstance(x, str) or not x.strip():
                out.append([]); continue
            s = x.replace("’","'").replace("—","-").strip()
            try:
                out.append(json.loads(s))
            except Exception:
                out.append([])
        return out

    df["choices_json"] = _coerce(df["choices_json"])
    df["facts_json"]   = _coerce(df["facts_json"])
    return df

# Load dataset (fail visibly if bad URL)
try:
    tracks = load_tracks(DATASET_URL)
except Exception as e:
    st.error(f"Failed to load dataset from DATASET_URL.\n{e}")
    st.stop()

# ------------------ STATE ------------------
if "mode" not in st.session_state:
    st.session_state.mode = "Daily Challenge"
if "reveal" not in st.session_state:
    st.session_state.reveal = 0
if "locked" not in st.session_state:
    st.session_state.locked = False
if "score" not in st.session_state:
    st.session_state.score = 0
if "streak" not in st.session_state:
    st.session_state.streak = 0
if "start_time" not in st.session_state:
    st.session_state.start_time = time.time()
if "guesses" not in st.session_state:
    st.session_state.guesses = 0
if "row" not in st.session_state:
    st.session_state.row = pick_daily_row(tracks)

# ------------------ UI ------------------
st.sidebar.title("🎵 SongSnap++")
st.sidebar.caption("Guess the song from a short audio snippet + emoji riddle.")
st.session_state.mode = st.sidebar.radio("Mode", ["Daily Challenge", "Arcade Pack: Mix"], index=0)
username = st.sidebar.text_input("Your name (for leaderboard):", value="", placeholder="e.g., Kathy")
st.sidebar.write(f"Streak: **{st.session_state.streak}**")
st.sidebar.write(f"Total score: **{st.session_state.score}**")

st.markdown("<h1 style='margin-top:0'>🎧 SongSnap++</h1>", unsafe_allow_html=True)

# Pick the row for this round
if st.session_state.mode == "Daily Challenge":
    st.session_state.row = pick_daily_row(tracks)
    date_str = datetime.now(CENTRAL).strftime("%Y-%m-%d")
else:
    st.session_state.row = new_round_row(tracks)
    date_str = "arcade"

row = st.session_state.row

# Emoji riddle
st.subheader("Emoji Riddle")
st.markdown(f"<div style='font-size:2.2rem'>{row['emoji']}</div>", unsafe_allow_html=True)

# Audio snippet (graceful when missing)
st.subheader("Audio Snippet")
levels = [
    (row.get("preview_1s_url",""), "🔊 1s tease"),
    (row.get("preview_3s_url",""), "🔊 3s reveal"),
    (row.get("preview_5s_url",""), "🔊 5s full clip"),
]
url, label = levels[st.session_state.reveal]
if not url:
    url = first_available_preview(row)
has_audio = bool(url)

st.caption(label if has_audio else "No audio preview for this track")
if has_audio:
    st.audio(url)
else:
    st.info("No preview available. You can still guess from the emoji hint!")

c1, c2 = st.columns(2)
with c1:
    if has_audio and st.session_state.reveal < 2 and not st.session_state.locked:
        if st.button("Add 2s & hint (−150 pts)"):
            st.session_state.reveal = min(2, st.session_state.reveal + 1)
    else:
        st.empty()
with c2:
    if st.session_state.reveal >= 1 or not has_audio:
        st.info(f"Context hint: {row['context_hint']}")
    else:
        st.caption("Context hint locked")

# Choices
st.subheader("Your Guess")
choices = row["choices_json"]
answer_idx = int(row["answer_idx"])

def guess(i: int):
    if st.session_state.locked:
        return
    st.session_state.locked = True
    st.session_state.guesses += 1
    correct = (i == answer_idx)
    delta = score_round(correct, st.session_state.reveal, st.session_state.start_time)
    st.session_state.score += delta

    if correct:
        st.balloons()
        st.success(f"✅ Correct! {row['title']} — {row['artist']}")
        st.session_state.streak += 1
        with st.expander("Pop-culture facts"):
            for f in row["facts_json"]:
                st.write("• " + f)
    else:
        st.error("❌ Not quite. Good try!")
        st.write(f"Answer: **{row['title']} — {row['artist']}**")
        st.session_state.streak = 0

    st.caption(f"Round points: **{delta}**")

    # Post-round: offer Spotify embed without spoiling earlier
    tid = extract_spotify_track_id(row.get("spotify_url", ""))
    if tid:
        with st.expander("🎧 Listen on Spotify"):
            components.html(spotify_embed_html(tid), height=180, scrolling=False)

    # Save score (Daily only)
    if sb_client and st.session_state.mode == "Daily Challenge" and username.strip():
        elapsed_ms = int((time.time() - st.session_state.start_time) * 1000)
        submit_score(date_str, username, st.session_state.score, st.session_state.streak,
                     st.session_state.guesses, st.session_state.reveal, elapsed_ms)

for idx, opt in enumerate(choices):
    st.button(opt, key=f"opt_{idx}", on_click=guess, args=(idx,), disabled=st.session_state.locked)

# Next
st.divider()
next_label = "Next Song ▶️" if has_audio else "Skip (No Preview) ▶️"
if st.button(next_label):
    st.session_state.reveal = 0
    st.session_state.locked = False
    st.session_state.start_time = time.time()
    st.session_state.guesses = 0
    if st.session_state.mode == "Arcade Pack: Mix":
        st.session_state.row = new_round_row(tracks)
    st.rerun()

# Leaderboard (Daily only)
if st.session_state.mode == "Daily Challenge":
    st.subheader("🏆 Today’s Leaderboard")
    if sb_client:
        lb = fetch_leaderboard(date_str)
        if len(lb) == 0:
            st.caption("Be the first on the board today!")
        else:
            st.dataframe(lb)
    else:
        st.caption("Set SUPABASE_URL and SUPABASE_ANON_KEY in secrets to enable the leaderboard.")
