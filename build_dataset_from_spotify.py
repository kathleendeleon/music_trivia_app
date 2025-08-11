
#sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
#    client_id="5eb70c8af7a24623b261e32b3f6b5c61",
#    client_secret="fbc7f1b7ca4a43de861d31478cbda899"
#))

# build_dataset_from_spotify.py
# Minimal builder for SongSnap++ dataset
# - Public playlists only (SpotifyClientCredentials)
# - Emoji riddles from TITLE ONLY (no audio_features calls)
# - Adds spotify_url for post-round embed in the app

import json, random, re
import pandas as pd
from urllib.parse import urlparse
from typing import List, Dict
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# ========= YOUR SPOTIFY APP CREDS (public playlists only) =========
CLIENT_ID = "YOUR_CLIENT_ID"
CLIENT_SECRET = "YOUR_CLIENT_SECRET"

# ========= INPUT / OUTPUT =========
PLAYLIST_URL = "https://open.spotify.com/playlist/5mKni0t3DLTaQJhC0sQsS4"
OUTPUT_CSV = "songsnap_from_spotify.csv"
PACK_NAME = "Playlist Import"
MARKET = "US"      # helps resolve region-locked tracks
MAX_TRACKS = 0     # 0 = no limit; set to small number for testing

# ---------- helpers ----------
def parse_playlist_id(url: str) -> str:
    if url.startswith("spotify:playlist:"):
        return url.split(":")[-1]
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    return parts[1] if len(parts) >= 2 and parts[0] == "playlist" else parts[-1]

def year_from_date(release_date: str):
    try:
        return int(release_date[:4])
    except Exception:
        return None

def choice_label(track_dict) -> str:
    artists = ", ".join([a["name"] for a in track_dict.get("artists", [])]) if track_dict.get("artists") else track_dict.get("artist","")
    return f"{track_dict['name']} — {artists}"

def build_choices(all_tracks: List[Dict], correct_idx: int, k: int = 3) -> List[str]:
    idxs = list(range(len(all_tracks)))
    random.shuffle(idxs)
    correct = choice_label(all_tracks[correct_idx])
    out = [correct]
    seen = {correct.lower()}
    for j in idxs:
        if j == correct_idx:
            continue
        cand = choice_label(all_tracks[j])
        if cand.lower() in seen:
            continue
        out.append(cand); seen.add(cand.lower())
        if len(out) >= k + 1:
            break
    random.shuffle(out)
    return out

def correct_answer_index(choices: List[str], correct_label: str) -> int:
    return next((i for i, c in enumerate(choices) if c == correct_label), 0)

# ---------- Title → Emoji ----------
TITLE_EMOJI_MAP = [
    (r"\b(love|heart|romance|kiss)\b", "❤️"),
    (r"\b(night|midnight|moon|dark)\b", "🌙"),
    (r"\b(day|sun|summer|heat|hot)\b", "☀️"),
    (r"\b(star|light|shine|bright)\b", "✨"),
    (r"\b(rain|tears|cry|sad)\b", "🌧️"),
    (r"\b(fire|burn|flame)\b", "🔥"),
    (r"\b(blue)\b", "🔵"),
    (r"\b(red)\b", "🔴"),
    (r"\b(gold|yellow)\b", "🟡"),
    (r"\b(road|drive|car|ride|highway)\b", "🚗"),
    (r"\b(city|town)\b", "🏙️"),
    (r"\b(ocean|sea|wave|beach)\b", "🌊"),
    (r"\b(dance|party|club)\b", "🕺"),
    (r"\b(king|queen|royal)\b", "👑"),
    (r"\b(phone|call|ring)\b", "📞"),
    (r"\b(angel)\b", "😇"),
    (r"\b(devil|bad)\b", "😈"),
    (r"\b(happy|joy|smile)\b", "😊"),
    (r"\b(cry|tears)\b", "😭"),
    (r"\b(young|youth)\b", "🧒"),
    (r"\b(rocket|space)\b", "🚀"),
    (r"\b(river|water)\b", "🏞️"),
    (r"\b(snow|winter|cold)\b", "❄️"),
]

def emoji_from_title(title: str) -> str:
    t = (title or "").lower()
    emojis = []
    for pat, emo in TITLE_EMOJI_MAP:
        if re.search(pat, t):
            emojis.append(emo)
        if len(emojis) >= 5:
            break
    if not emojis:
        emojis = ["🎵","✨","🎧"][:random.randint(2,3)]
    # de-dup, preserve order, cap 5
    seen, final = set(), []
    for e in emojis:
        if e not in seen:
            final.append(e); seen.add(e)
    return "".join(final[:5])

# ---------- main ----------
def main():
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET
    ))

    playlist_id = parse_playlist_id(PLAYLIST_URL)

    # Fetch tracks with paging
    results = sp.playlist_tracks(playlist_id, market=MARKET)
    if not results or "items" not in results:
        raise SystemExit("Failed to fetch playlist items. Is it public?")

    items = results.get("items") or []
    while results.get("next"):
        results = sp.next(results)
        items.extend(results.get("items") or [])

    # Flatten tracks (skip local/episodes)
    raw_tracks = []
    for it in items:
        t = (it or {}).get("track") or {}
        if not t or t.get("is_local") or t.get("type") != "track":
            continue
        raw_tracks.append({
            "id": t.get("id"),
            "name": t.get("name") or "",
            "artists": [{"id": a.get("id"), "name": a.get("name")} for a in (t.get("artists") or [])],
            "album": (t.get("album") or {}).get("name", ""),
            "release_date": (t.get("album") or {}).get("release_date", ""),
            "year": year_from_date((t.get("album") or {}).get("release_date", "")),
            "preview_url": t.get("preview_url") or "",  # may be empty
            "popularity": t.get("popularity", 0),
            "external_url": (t.get("external_urls") or {}).get("spotify", "")
        })

    if MAX_TRACKS > 0:
        raw_tracks = raw_tracks[:MAX_TRACKS]

    if not raw_tracks:
        raise SystemExit("No tracks found after filtering.")

    # Build dataset rows
    rows = []
    for i, tr in enumerate(raw_tracks):
        title = tr["name"]
        artist = ", ".join([a["name"] for a in tr["artists"]])
        emoji = emoji_from_title(title)

        correct = choice_label(tr)
        choices = build_choices(raw_tracks, i, k=3)
        if correct not in choices:
            choices[random.randrange(len(choices))] = correct
        answer_idx = correct_answer_index(choices, correct)

        facts = []
        facts.append(f"From album: {tr['album']}" if tr['album'] else "Single release")
        if tr["year"]:
            facts.append(f"Release year: {tr['year']}")
        if tr["popularity"]:
            facts.append(f"Spotify popularity: {tr['popularity']}/100")

        prev = tr["preview_url"]  # could be empty; app will handle it

        rows.append({
            "id": f"sp_{i:04d}",
            "title": title,
            "artist": artist,
            "emoji": emoji,
            "choices_json": json.dumps(choices, ensure_ascii=False),
            "answer_idx": answer_idx,
            "preview_1s_url": prev,
            "preview_3s_url": prev,
            "preview_5s_url": prev,
            "context_hint": f"From '{tr['album']}' ({tr['year']})" if tr["album"] else (f"Released in {tr['year']}" if tr["year"] else "Album info not available"),
            "facts_json": json.dumps(facts, ensure_ascii=False),
            "pack": PACK_NAME,
            "year": tr["year"] or "",
            "tv_movie_ref": "",
            "spotify_url": tr["external_url"] or ""   # used for embed after the round
        })

    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"Saved {len(df)} tracks to {OUTPUT_CSV}")
    print(df.head(5).to_string(index=False))

if __name__ == "__main__":
    main()
