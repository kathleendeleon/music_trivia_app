import os, time, json, random, hashlib, re
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
import pandas as pd
import streamlit.components.v1 as components

# --- CONFIG ---
st.set_page_config(page_title="SongSnap++", page_icon="🎵", layout="centered")

# --- ENV (Supabase optional but recommended) ---
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
sb_client = None
if SUPABASE_URL and SUPABASE_ANON_KEY:
    from supabase import create_client, Client
    sb_client: "Client" = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# --- HELPERS for Spotify embeds ---
def extract_spotify_track_id(spotify_url: str | None) -> str | None:
    if not spotify_url or not isinstance(spotify_url, str):
        return None
    m = re.search(r"spotify\.com/track/([A-Za-z0-9]+)", spotify_url)
    if m:
        return m.group(1)
    m = re.search(r"spotify:track:([A-Za-z0-9]+)", spotify_url)
    return m.group(1) if m else None

def to_spotify_embed_url(spotify_url: str | None) -> str | None:
    tid = extract_spotify_track_id(spotify_url)
    if not tid:
        return None
    return f"https://open.spotify.com/embed/track/{tid}?utm_source=generator"

import streamlit.components.v1 as components

def embed_spotify_minimal(spotify_url):
    """Render a tiny Spotify embed (1x1). Safe on Streamlit Cloud."""
    try:
        # Convert any track link/URI to an embed URL
        tid = None
        if isinstance(spotify_url, str):
            if "spotify.com/track/" in spotify_url:
                tid = spotify_url.split("/track/")[1].split("?")[0]
            elif "spotify:track:" in spotify_url:
                tid = spotify_url.split("spotify:track:")[1].split("?")[0]

        if not tid:
            return

        embed_url = f"https://open.spotify.com/embed/track/{tid}?utm_source=generator"
        # Use the built-in iframe helper (no custom HTML parsing)
        components.iframe(embed_url, width=1, height=1, scrolling=False)
    except Exception as e:
        # Don’t crash the app—just show a quiet note for debugging
        st.caption(f"(Spotify embed unavailable: {e})")


def is_valid_audio_url(url: str) -> bool:
    return isinstance(url, str) and url.strip().lower().startswith("http")

def first_available_preview(row: pd.Series) -> str:
    for key in ["preview_1s_url", "preview_3s_url", "preview_5s_url"]:
        if is_valid_audio_url(row.get(key, "")):
            return row[key]
    return ""

# --- DATA ---
@st.cache_data
def load_tracks(path: str):
    df = pd.read_csv(path) 
    for c in ["facts_json", "choices_json"]:
        if c in df.columns:
            df[c] = df[c].apply(lambda x: json.loads(x.replace("’","'").replace("—","-")) if isinstance(x,str) else [])
    # normalize preview & spotify URL cols
    for c in ["preview_1s_url", "preview_3s_url", "preview_5s_url", "spotify_url"]:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str)
    return df

tracks = load_tracks("https://raw.githubusercontent.com/kathleendeleon/music_trivia_app/refs/heads/main/songsnap_from_spotify3.csv")

# --- UTILS ---
CENTRAL = ZoneInfo("America/Chicago")
def today_seed():
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
    penalty_time = int(50 * (elapsed // 5))
    emoji_perfect = 200 if reveal_level == 0 else 0
    return 50 if not correct else max(50, base - penalty_reveal - penalty_time + emoji_perfect)
def submit_score(date_str, username, score, streak, guesses, reveal_level, elapsed_ms):
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

# --- SIDEBAR ---
st.sidebar.title("🎵 SongSnap++")
st.sidebar.caption("Guess the song from a short audio snippet + emoji riddle.")
st.session_state.mode = st.sidebar.radio("Mode", ["Daily Challenge", "Arcade Pack: Mix"], index=0)
username = st.sidebar.text_input("Your name (for leaderboard):", value="", placeholder="e.g., Kathy")
st.sidebar.write(f"Streak: **{st.session_state.streak}**")
st.sidebar.write(f"Total score: **{st.session_state.score}**")

# --- HEADER ---
st.markdown("<h1 style='margin-top:0'>🎧 SongSnap++</h1>", unsafe_allow_html=True)

# Pick row
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
    (row.get("preview_1s_url",""), "🔊 1s tease"),
    (row.get("preview_3s_url",""), "🔊 3s reveal"),
    (row.get("preview_5s_url",""), "🔊 5s full clip"),
]
levels = [((u if isinstance(u, str) else ""), lbl) for (u, lbl) in levels]
url, label = levels[st.session_state.reveal]
if not url:
    url = first_available_preview(row)
has_audio = is_valid_audio_url(url)
st.caption(label if has_audio else "No audio preview for this track")

if has_audio:
    st.audio(url)
else:
    st.info("No preview available. You can still guess from the emoji hint!")

# Reveal / hint controls
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

# Hidden Spotify fallback
if not has_audio:
    sp_url = row.get("spotify_url", "")
    if to_spotify_embed_url(sp_url):
        play_flag_key = f"play_spotify_{row.get('id', random.randint(0, 1_000_000))}"
        if play_flag_key not in st.session_state:
            st.session_state[play_flag_key] = False
        if not st.session_state[play_flag_key]:
            if st.button("▶️ Play via Spotify (hidden)"):
                st.session_state[play_flag_key] = True
                st.rerun()
        if st.session_state[play_flag_key]:
            embed_spotify_minimal(sp_url)
            st.caption("Playing via Spotify in the background (tiny embedded player).")

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

# --- LEADERBOARD ---
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
