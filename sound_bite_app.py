import os, time, json, random, hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
import pandas as pd

# --- CONFIG ---
st.set_page_config(page_title="SongSnap++", page_icon="🎵", layout="centered")

# --- ENV (Supabase optional but recommended) ---
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

# Lazy import only if keys exist
sb_client = None
if SUPABASE_URL and SUPABASE_ANON_KEY:
    from supabase import create_client, Client
    sb_client: "Client" = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# --- DATA ---
@st.cache_data
def load_tracks(path: str):
    df = pd.read_csv(path) 
    # coerce json-like columns
    for c in ["facts_json", "choices_json"]:
        df[c] = df[c].apply(lambda x: json.loads(x.replace("’","'").replace("—","-")) if isinstance(x,str) else [])
    return df

tracks = load_tracks("songsnap_starter_40.csv")  # place CSV next to .py or use full path

# --- UTILS ---
CENTRAL = ZoneInfo("America/Chicago")

def today_seed():
    # Everyone sees same track per day in America/Chicago
    d = datetime.now(CENTRAL).strftime("%Y-%m-%d")
    return int(hashlib.sha256(d.encode()).hexdigest(), 16)

def pick_daily_row(df: pd.DataFrame) -> pd.Series:
    rng = random.Random(today_seed())
    idx = rng.randrange(len(df))
    return df.iloc[idx]

def new_round_row(df: pd.DataFrame) -> pd.Series:
    return df.sample(1, random_state=random.randint(0, 10_000)).iloc[0]

def score_round(correct: bool, reveal_level: int, start_time: float) -> int:
    elapsed = time.time() - start_time
    base = 1000
    penalty_reveal = 150 * reveal_level
    penalty_time = int(50 * (elapsed // 5))  # -50 per 5s chunk
    emoji_perfect = 200 if reveal_level == 0 else 0
    return 50 if not correct else max(50, base - penalty_reveal - penalty_time + emoji_perfect)

def submit_score(date_str: str, username: str, score: int, streak: int, guesses: int, reveal_level: int, elapsed_ms: int):
    if not sb_client:
        return None
    payload = {
        "date": date_str,
        "username": username.strip()[:32],
        "score": score,
        "streak": streak,
        "guesses": guesses,
        "reveal_level": reveal_level,
        "elapsed_ms": elapsed_ms,
    }
    # Upsert on (date, username) keeping the best score
    # Strategy: fetch existing, keep max
    existing = sb_client.table("songsnap_scores").select("*").eq("date", date_str).eq("username", payload["username"]).execute()
    if existing.data:
        best = max(existing.data[0]["score"], score)
        payload["score"] = best
        sb_client.table("songsnap_scores").update(payload).eq("date", date_str).eq("username", payload["username"]).execute()
    else:
        sb_client.table("songsnap_scores").insert(payload).execute()

def fetch_leaderboard(date_str: str, limit: int = 25):
    if not sb_client:
        return pd.DataFrame()
    res = sb_client.table("songsnap_scores") \
        .select("username,score,streak,guesses,reveal_level,elapsed_ms") \
        .eq("date", date_str) \
        .order("score", desc=True) \
        .order("elapsed_ms", desc=False) \
        .limit(limit) \
        .execute()
    return pd.DataFrame(res.data)

# --- STATE ---
if "mode" not in st.session_state:
    st.session_state.mode = "Daily Challenge"
if "reveal" not in st.session_state:
    st.session_state.reveal = 0   # 0=1s, 1=3s, 2=5s
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

# --- SIDEBAR ---
st.sidebar.title("🎵 SongSnap++")
st.sidebar.caption("Guess the song from a short audio snippet + emoji riddle.")
st.session_state.mode = st.sidebar.radio("Mode", ["Daily Challenge", "Arcade Pack: Mix"], index=0)
username = st.sidebar.text_input("Your name (for leaderboard):", value="", placeholder="e.g., Kathy")
st.sidebar.write(f"Streak: **{st.session_state.streak}**")
st.sidebar.write(f"Total score: **{st.session_state.score}**")

# --- HEADER ---
st.markdown("<h1 style='margin-top:0'>🎧 SongSnap++</h1>", unsafe_allow_html=True)

# Pick the row
if st.session_state.mode == "Daily Challenge":
    st.session_state.row = pick_daily_row(tracks)
    date_str = datetime.now(CENTRAL).strftime("%Y-%m-%d")
else:
    st.session_state.row = new_round_row(tracks)
    date_str = "arcade"

row = st.session_state.row

# --- EMOJI RIDDLE ---
st.subheader("Emoji Riddle")
st.markdown(f"<div style='font-size:2.2rem'>{row['emoji']}</div>", unsafe_allow_html=True)

# --- AUDIO ---
st.subheader("Audio Snippet")
levels = [
    (row["preview_1s_url"], "🔊 1s tease"),
    (row["preview_3s_url"], "🔊 3s reveal"),
    (row["preview_5s_url"], "🔊 5s full clip"),
]
url, label = levels[st.session_state.reveal]
st.caption(f"{label} — extending reveals costs points")
st.audio(url)

c1, c2 = st.columns(2)
with c1:
    extend = st.button("Add 2s & hint (−150 pts)", disabled=st.session_state.reveal>=2 or st.session_state.locked)
    if extend:
        st.session_state.reveal = min(2, st.session_state.reveal + 1)
with c2:
    if st.session_state.reveal >= 1:
        st.info(f"Context hint: {row['context_hint']}")
    else:
        st.caption("Context hint locked")

# --- CHOICES ---
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
        st.error("❌ Not quite. That’s like calling *1989* Taylor’s debut.")
        st.write(f"Answer: **{row['title']} — {row['artist']}**")
        st.session_state.streak = 0

    st.caption(f"Round points: **{delta}**")
    # Save score (Daily only)
    if sb_client and st.session_state.mode == "Daily Challenge" and username.strip():
        elapsed_ms = int((time.time() - st.session_state.start_time) * 1000)
        submit_score(date_str, username, st.session_state.score, st.session_state.streak,
                     st.session_state.guesses, st.session_state.reveal, elapsed_ms)

for idx, opt in enumerate(choices):
    st.button(opt, key=f"opt_{idx}", on_click=guess, args=(idx,), disabled=st.session_state.locked)

st.divider()
if st.button("Next Song ▶️"):
    st.session_state.reveal = 0
    st.session_state.locked = False
    st.session_state.start_time = time.time()
    st.session_state.guesses = 0
    if st.session_state.mode == "Arcade Pack: Mix":
        st.session_state.row = new_round_row(tracks)
    st.rerun()

# --- LEADERBOARD (Daily only) ---
if st.session_state.mode == "Daily Challenge":
    st.subheader("🏆 Today’s Leaderboard")
    if sb_client:
        lb = fetch_leaderboard(date_str)
        if len(lb) == 0:
            st.caption("Be the first on the board today!")
        else:
            st.dataframe(lb)
    else:
        st.caption("Set SUPABASE_URL and SUPABASE_ANON_KEY to enable the leaderboard.")
