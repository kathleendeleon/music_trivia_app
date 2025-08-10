import os, json, random, re
import pandas as pd
from urllib.parse import urlparse
from datetime import datetime
from typing import List, Dict

# pip install spotipy or put in requirements.txt
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# -------- CONFIG --------
# Set these in your shell before running:
# export SPOTIFY_CLIENT_ID=xxx
# export SPOTIFY_CLIENT_SECRET=yyy
# export SPOTIFY_REDIRECT_URI=http://localhost:8080/callback

PLAYLIST_URL = os.getenv("PLAYLIST_URL", "https://open.spotify.com/playlist/5Op8h4120je5thN58Qaeyo") #pick your playlist
OUTPUT_CSV = os.getenv("OUTPUT_CSV", "songsnap_from_spotify.csv")
PACK_NAME = os.getenv("PACK_NAME", "Playlist Import")  # shows up in app
SCOPES = "playlist-read-private"

# Optional: limit max tracks for testing (0 = no cap)
MAX_TRACKS = int(os.getenv("MAX_TRACKS", "0"))

# -------- Helpers --------
def parse_playlist_id(url: str) -> str:
    # Accept both open.spotify.com and spotify:playlist:ID formats
    if url.startswith("spotify:playlist:"):
        return url.split(":")[-1]
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    # /playlist/<id>
    return parts[1] if len(parts) >= 2 and parts[0] == "playlist" else parts[-1]

EMOJI_MAP = [
    (r"\b(love|heart)\b", "❤️"),
    (r"\b(night|midnight|moon)\b", "🌙"),
    (r"\b(star|light|shine)\b", "✨"),
    (r"\b(sun|summer|hot)\b", "☀️"),
    (r"\b(rain|tears|sad)\b", "🌧️"),
    (r"\b(fire|burn|flame)\b", "🔥"),
    (r"\b(blue)\b", "🔵"),
    (r"\b(red)\b", "🔴"),
    (r"\b(dance|party)\b", "🕺"),
    (r"\b(cry|tears)\b", "😭"),
    (r"\b(king|queen)\b", "👑"),
    (r"\b(road|drive|car|ride)\b", "🚗"),
    (r"\b(city|town)\b", "🏙️"),
    (r"\b(ocean|sea|wave)\b", "🌊"),
]

def make_emojis(title: str) -> str:
    t = title.lower()
    chosen = []
    for pat, emo in EMOJI_MAP:
        if re.search(pat, t):
            chosen.append(emo)
        if len(chosen) >= 4:
            break
    if not chosen:
        chosen = ["🎵","✨","🎧"][:random.randint(2,3)]
    return "".join(chosen)

def choice_label(track) -> str:
    artists = ", ".join([a["name"] for a in track["artists"]]) if track.get("artists") else track.get("artist","")
    return f"{track['name']} — {artists}"

def year_from_date(release_date: str) -> int:
    # release_date can be "YYYY", "YYYY-MM-DD", or "YYYY-MM"
    try:
        return int(release_date[:4])
    except Exception:
        return None

def build_choices(all_tracks: List[Dict], correct_idx: int, k: int = 3) -> List[str]:
    # pick 3 distractors from the same playlist, avoiding identical titles/artists
    idxs = list(range(len(all_tracks)))
    random.shuffle(idxs)
    out = [choice_label(all_tracks[correct_idx])]
    added = 0
    seen = set([out[0].lower()])
    for j in idxs:
        if j == correct_idx: 
            continue
        cand = choice_label(all_tracks[j])
        if cand.lower() in seen:
            continue
        out.append(cand)
        seen.add(cand.lower())
        added += 1
        if added >= k:
            break
    random.shuffle(out)
    return out

def correct_answer_index(choices: List[str], correct_label: str) -> int:
    for i, c in enumerate(choices):
        if c == correct_label:
            return i
    return 0

# -------- Main export --------
def main():
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(scope=SCOPES))
    playlist_id = parse_playlist_id(PLAYLIST_URL)

    # Pull playlist metadata
    pl = sp.playlist(playlist_id, fields="name,owner(display_name),tracks.items(track(name,artists(name),album(name,release_date),preview_url,popularity,external_urls,external_ids,is_local)),tracks.next")
    playlist_name = pl.get("name","Playlist")
    items = pl["tracks"]["items"]
    next_url = pl["tracks"]["next"]

    while next_url:
        next_page = sp.next({"next": next_url})
        items.extend(next_page["items"])
        next_url = next_page["next"]

    # Flatten tracks
    tracks = []
    for it in items:
        t = it.get("track")
        if not t or t.get("is_local"):  # skip local files
            continue
        name = t.get("name") or ""
        artists = [a["name"] for a in t.get("artists", [])] or []
        album = t.get("album", {})
        release_date = album.get("release_date") or ""
        preview = t.get("preview_url")  # 30s preview if available
        popularity = t.get("popularity", 0)
        isrc = (t.get("external_ids") or {}).get("isrc", "")
        tracks.append({
            "name": name,
            "artists": artists,
            "album": album.get("name",""),
            "release_date": release_date,
            "year": year_from_date(release_date),
            "preview_url": preview,
            "popularity": popularity,
            "isrc": isrc,
            "external_url": (t.get("external_urls") or {}).get("spotify",""),
        })

    if MAX_TRACKS > 0:
        tracks = tracks[:MAX_TRACKS]

    if not tracks:
        raise SystemExit("No tracks found. Is the playlist private? Ensure your scopes and account are correct.")

    rows = []
    for i, tr in enumerate(tracks):
        title = tr["name"]
        artist = ", ".join(tr["artists"]) if tr["artists"] else ""
        emoji = make_emojis(title)
        correct = choice_label(tr)
        choices = build_choices(tracks, i, k=3)
        # ensure correct included
        if correct not in choices:
            choices[random.randrange(len(choices))] = correct
        answer_idx = correct_answer_index(choices, correct)

        # “Facts” are lightweight and auto-generated; you can hand-edit later
        facts = [
            f"From album: {tr['album']}" if tr['album'] else "Single release",
            f"Release year: {tr['year']}" if tr['year'] else "Release year unknown",
            f"Spotify popularity: {tr['popularity']}/100"
        ]

        # For SongSnap++ schema, we need 1s/3s/5s. If you only have 30s preview:
        # write the same preview URL into all three (good enough for MVP).
        prev = tr["preview_url"] or ""
        preview_1s = preview_3s = preview_5s = prev

        rows.append({
            "id": f"sp_{i:04d}",
            "title": title,
            "artist": artist,
            "emoji": emoji,
            "choices_json": json.dumps(choices, ensure_ascii=False),
            "answer_idx": answer_idx,
            "preview_1s_url": preview_1s,
            "preview_3s_url": preview_3s,
            "preview_5s_url": preview_5s,
            "context_hint": f"From '{tr['album']}' ({tr['year']})" if tr["album"] else (f"Released in {tr['year']}" if tr["year"] else "Album info not available"),
            "facts_json": json.dumps(facts, ensure_ascii=False),
            "pack": PACK_NAME if PACK_NAME else playlist_name,
            "year": tr["year"] or "",
            "tv_movie_ref": ""  # optional free text
        })

    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"Saved {len(df)} tracks to {OUTPUT_CSV}")

if __name__ == "__main__":
    main()
