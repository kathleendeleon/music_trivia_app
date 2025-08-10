# build_dataset_from_spotify.py
# Creates SongSnap++ CSV from a Spotify playlist
# - Hard-coded SpotifyClientCredentials (public playlists only)
# - Emoji riddles from title keywords + artist genres + audio features
# - Choice options auto-generated from the same playlist
# - Safe fallback when previews are missing

import json, random, re, math
import pandas as pd
from urllib.parse import urlparse
from typing import List, Dict
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# ========= HARD-CODED SPOTIFY APP CREDENTIALS (public playlists only) =========
CLIENT_ID = "YOUR_CLIENT_ID"
CLIENT_SECRET = "YOUR_CLIENT_SECRET"

# Playlist & output
PLAYLIST_URL = "https://open.spotify.com/playlist/5Op8h4120je5thN58Qaeyo?si=U41i1n9rRLWtm8d-cCDQYg"
OUTPUT_CSV = "songsnap_from_spotify.csv"
PACK_NAME = "Playlist Import"

# Optional: limit tracks for testing (0 = no limit)
MAX_TRACKS = 0

# ---------- Helpers ----------
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
    artists = ", ".join([a["name"] for a in track_dict["artists"]]) if track_dict.get("artists") else track_dict.get("artist","")
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
        out.append(cand)
        seen.add(cand.lower())
        if len(out) >= k + 1:
            break
    random.shuffle(out)
    return out

def correct_answer_index(choices: List[str], correct_label: str) -> int:
    return next((i for i, c in enumerate(choices) if c == correct_label), 0)

# ---------- Emoji Riddle Builders ----------
TITLE_EMOJI_MAP = [
    (r"\b(love|heart|romance|kiss)\b", "❤️"),
    (r"\b(night|midnight|moon|dark)\b", "🌙"),
    (r"\b(day|sun|summer|hot|heat)\b", "☀️"),
    (r"\b(star|light|shine|bright)\b", "✨"),
    (r"\b(rain|tears|cry|sad)\b", "🌧️"),
    (r"\b(fire|burn|flame|hot)\b", "🔥"),
    (r"\b(blue|sad)\b", "🔵"),
    (r"\b(red)\b", "🔴"),
    (r"\b(gold|yellow)\b", "🟡"),
    (r"\b(road|drive|car|ride|highway)\b", "🚗"),
    (r"\b(city|town)\b", "🏙️"),
    (r"\b(ocean|sea|wave|beach)\b", "🌊"),
    (r"\b(dance|party|club)\b", "🕺"),
    (r"\b(king|queen|royal)\b", "👑"),
    (r"\b(phone|call|ring)\b", "📞"),
    (r"\b(angel|devil)\b", "😇"),
    (r"\b(devil|bad)\b", "😈"),
    (r"\b(happy|joy|smile)\b", "😊"),
    (r"\b(cry|tears)\b", "😭"),
    (r"\b(young|youth)\b", "🧒"),
]

GENRE_EMOJI_MAP = [
    ("k-pop", "🇰🇷"),
    ("emo", "🖤"),
    ("pop punk", "🧷"),
    ("indie", "🌿"),
    ("hip hop", "🎤"),
    ("rap", "🎤"),
    ("r&b", "🎶"),
    ("latin", "🪇"),
    ("dance", "💃"),
    ("edm", "🎛️"),
    ("house", "🏠"),
    ("country", "🤠"),
    ("jazz", "🎺"),
    ("rock", "🎸"),
    ("metal", "🤘"),
    ("soul", "🫀"),
    ("funk", "🕶️"),
]

def emoji_from_title(title: str) -> List[str]:
    t = title.lower()
    out = []
    for pat, emo in TITLE_EMOJI_MAP:
        if re.search(pat, t):
            out.append(emo)
        if len(out) >= 3:
            break
    return out

def emoji_from_genres(genres: List[str]) -> List[str]:
    g = ", ".join(genres).lower()
    out = []
    for key, emo in GENRE_EMOJI_MAP:
        if key in g:
            out.append(emo)
        if len(out) >= 2:
            break
    return out

def emoji_from_audio_features(af: Dict) -> List[str]:
    if not af:
        return []
    out = []
    # Danceability
    if af.get("danceability", 0) >= 0.7:
        out.append("🕺")
    # Energy
    if af.get("energy", 0) >= 0.7:
        out.append("⚡")
    # Valence (happiness)
    val = af.get("valence", 0.0)
    if val >= 0.65:
        out.append("😊")
    elif val <= 0.35:
        out.append("😢")
    # Acousticness
    if af.get("acousticness", 0) >= 0.6:
        out.append("🎻")
    # Tempo
    tempo = af.get("tempo", 0)
    if tempo:
        if tempo >= 130:
            out.append("⏱️")
        elif tempo <= 80:
            out.append("🐢")
    # Keep it tight
    return out[:3]

def synth_emoji_riddle(title: str, genres: List[str], audio_features: Dict) -> str:
    parts = []
    parts += emoji_from_title(title)
    parts += emoji_from_genres(genres)
    parts += emoji_from_audio_features(audio_features)
    # fallback if we somehow got nothing
    if not parts:
        parts = ["🎵","✨","🎧"]
    # de-dup and cap length
    seen, final = set(), []
    for p in parts:
        if p not in seen:
            final.append(p)
            seen.add(p)
        if len(final) >= 5:
            break
    return "".join(final)

# ---------- MAIN ----------
def main():
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=CLIENT_ID, client_secret=CLIENT_SECRET))
    playlist_id = parse_playlist_id(PLAYLIST_URL)

    # Pull playlist tracks
    results = sp.playlist_tracks(playlist_id)
    items = results["items"]
    while results["next"]:
        results = sp.next(results)
        items.extend(results["items"])

    # Flatten minimal track info
    raw_tracks = []
    artist_ids = set()
    track_ids = []
    for it in items:
        t = it.get("track")
        if not t or t.get("is_local"):
            continue
        track_ids.append(t["id"])
        artist_ids.update([a["id"] for a in t.get("artists", []) if a and a.get("id")])

        raw_tracks.append({
            "id": t.get("id"),
            "name": t.get("name") or "",
            "artists": [{"id": a["id"], "name": a["name"]} for a in t.get("artists", [])],
            "album": t.get("album", {}).get("name", ""),
            "release_date": t.get("album", {}).get("release_date", ""),
            "year": year_from_date(t.get("album", {}).get("release_date", "")),
            "preview_url": t.get("preview_url") or "",
            "popularity": t.get("popularity", 0),
            "external_url": (t.get("external_urls") or {}).get("spotify", "")
        })

    if MAX_TRACKS > 0:
        raw_tracks = raw_tracks[:MAX_TRACKS]
        track_ids = [t["id"] for t in raw_tracks if t["id"]]

    if not raw_tracks:
        raise SystemExit("No tracks found (is the playlist private?).")

    # Fetch audio features in batches
    audio_features_map: Dict[str, Dict] = {}
    for i in range(0, len(track_ids), 100):
        batch = track_ids[i:i+100]
        feats = sp.audio_features(batch)
        for f in feats:
            if f and f.get("id"):
                audio_features_map[f["id"]] = f

    # Fetch artist genres (batch lookup)
    artist_id_list = [a for a in artist_ids if a]
    artist_genre_map: Dict[str, List[str]] = {}
    for i in range(0, len(artist_id_list), 50):
        batch = artist_id_list[i:i+50]
        artists = sp.artists(batch)["artists"]
        for a in artists:
            artist_genre_map[a["id"]] = a.get("genres", []) or []

    # Build rows
    rows = []
    for i, tr in enumerate(raw_tracks):
        # merge all artist genres for this track
        genres = []
        for a in tr["artists"]:
            genres += artist_genre_map.get(a["id"], [])
        # synthesize emoji riddle
        emojis = synth_emoji_riddle(tr["name"], genres, audio_features_map.get(tr["id"], {}))

        # correct + choices
        correct = choice_label(tr)
        choices = build_choices(raw_tracks, i, k=3)
        if correct not in choices:
            choices[random.randrange(len(choices))] = correct
        answer_idx = correct_answer_index(choices, correct)

        # quick facts using audio features
        af = audio_features_map.get(tr["id"], {}) or {}
        tempo = af.get("tempo")
        dance = af.get("danceability")
        energy = af.get("energy")
        val = af.get("valence")
        facts = []
        facts.append(f"From album: {tr['album']}" if tr['album'] else "Single release")
        if tr['year']:
            facts.append(f"Release year: {tr['year']}")
        if tempo:
            facts.append(f"Tempo: {int(round(tempo))} BPM")
        if dance is not None:
            facts.append(f"Danceability: {int(round(dance*100))}/100")
        if energy is not None:
            facts.append(f"Energy: {int(round(energy*100))}/100")
        if val is not None:
            facts.append(f"Mood (valence): {int(round(val*100))}/100")
        if tr["popularity"]:
            facts.append(f"Spotify popularity: {tr['popularity']}/100")

        prev = tr["preview_url"]  # may be blank if Spotify has no preview
        rows.append({
            "id": f"sp_{i:04d}",
            "title": tr["name"],
            "artist": ", ".join([a["name"] for a in tr["artists"]]),
            "emoji": emojis,
            "choices_json": json.dumps(choices, ensure_ascii=False),
            "answer_idx": answer_idx,
            "preview_1s_url": prev,
            "preview_3s_url": prev,
            "preview_5s_url": prev,
            "context_hint": f"From '{tr['album']}' ({tr['year']})" if tr["album"] else (f"Released in {tr['year']}" if tr["year"] else "Album info not available"),
            "facts_json": json.dumps(facts, ensure_ascii=False),
            "pack": PACK_NAME,
            "year": tr["year"] or "",
            "tv_movie_ref": ""
        })

    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"Saved {len(df)} tracks to {OUTPUT_CSV}")
    # Optional: preview first few
    print(df.head(5).to_string(index=False))

if __name__ == "__main__":
    main()
