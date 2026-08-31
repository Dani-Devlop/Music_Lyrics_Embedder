#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
metafetcher.py — Music_Lyrics_Embedder add-on
=============================================
1) Filename parsing   -> artist/title from filename
2) Original covers    -> original cover (Deezer cover_xl); embedded if file has no cover
3) Spam cleaner       -> remove @handles, websites (bingo.com / www.bbangi.com / t.me/...),
                         (Official Video), 320kbps etc. from tags and filename
4) song_meta()        -> complete metadata from Deezer (fixed version of your own function)
5) Failure report     -> files that couldn't be identified anywhere are reported at the end

CLI:
    python metafetcher.py "D:/Music" -r
    python metafetcher.py "song.mp3" --dry-run
"""

from __future__ import annotations

import os
import re
import time
import argparse
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

import requests
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (APIC, COMM, ID3, TALB, TBPM, TCON, TDRC, TIT2,
                         TLEN, TPE1, TPOS, TRCK, TSRC, USLT)

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DEEZER_API          = "https://api.deezer.com"
LRCLIB_API          = "https://lrclib.net/api"
HTTP_TIMEOUT        = 10
HTTP_RETRIES        = 2
SLEEP_BETWEEN_FILES = 0.15
AUDIO_EXTS          = {".mp3", ".flac"}

MIN_MATCH_SCORE = 0.34        # 0..1 — strictness for Deezer result matching; 0 = always accept
FAILED_REPORT   = "failed_songs.txt"

EXTRA_JUNK_WORDS: List[str] = [
    # Add known spam channel/site names here, e.g., "rapfa", "bbangi"
]

# --------------------------------------------------------------------------- #
# 1) Spam / junk cleaning   (requirement #3)
# --------------------------------------------------------------------------- #
_TLDS = ("com|net|org|ir|info|xyz|online|site|website|shop|store|link|app|dev|"
         "tv|cc|io|co|biz|live|vip|us|ru|club|fun|click|top|space|pro|art|page|blog")

URL_RE = re.compile(
    rf"(?:https?://)?(?:www\.)?[a-z0-9\u0600-\u06FF\-]+(?:\.[a-z0-9\u0600-\u06FF\-]+)*"
    rf"\.(?:{_TLDS})(?:/\S*)?",
    re.IGNORECASE,
)
HANDLE_RE = re.compile(r"@[A-Za-z0-9_\u0600-\u06FF]+")
TG_RE     = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/\S+", re.IGNORECASE)
JOIN_RE   = re.compile(
    r"(?:join\s+(?:us|our\s+channel)|follow\s+us|subscribe\s+(?:to\s+)?our|"
    r"کانال\s+(?:تلگرام|ایتا)|تلگرام\s+ما|عضویت\s+در\s+کانال)[^\n]{0,30}",
    re.IGNORECASE,
)
PERSIAN_JUNK_RE = re.compile(
    r"دانلود\s+(?:آهنگ|اهنگ)(?:\s+(?:با\s+کیفیت|جدید))?|پخش\s+آنلاین|آپلود\s+شده\s+در",
)
BRACKET_JUNK_RE = re.compile(
    r"[\(\[\{][^)\]\}]{0,60}?(?:official|lyrics?|video|audio|hd|hq|4k|remaster|"
    r"explicit|full\s+album|free\s+download|download|promo|out\s+now|320|128|"
    r"دانلود|کیفیت|کاور)[^)\]\}]{0,60}?[\)\]\}]",
    re.IGNORECASE,
)
BITRATE_RE    = re.compile(r"\b\d{2,4}\s*k?bps\b", re.IGNORECASE)
EMPTY_PAIR_RE = re.compile(r"\s*[\(\[\{]\s*[-–—:;,.|]*\s*[\)\]\}]")
MULTISPACE_RE = re.compile(r"\s+")

_SPAM_NAME_RE = (
    re.compile(rf"\b(?:{'|'.join(re.escape(w) for w in EXTRA_JUNK_WORDS)})\b", re.IGNORECASE)
    if EXTRA_JUNK_WORDS else re.compile(r"(?!x)x")   # never matches
)


def clean_text(text: Optional[str], deep: bool = False) -> str:
    """
    Remove spam from tag text.

    deep=False : light cleaning (@, URL, t.me) — for tags that get written
    deep=True  : + remove (Official Video), 320kbps, "دانلود آهنگ" etc.
                 — for filenames and search queries
    Examples:
        "Rap Game @test"        -> "Rap Game"
        "Rap Game bingo.com"    -> "Rap Game"
        "Rap Game www.bbangi.com" -> "Rap Game"
    """
    if not text:
        return ""
    out = str(text)
    for _ in range(2):                          # two passes for nested spam
        prev = out
        out = TG_RE.sub(" ", out)
        out = URL_RE.sub(" ", out)
        out = HANDLE_RE.sub(" ", out)
        out = _SPAM_NAME_RE.sub(" ", out)
        if deep:
            out = JOIN_RE.sub(" ", out)
            out = PERSIAN_JUNK_RE.sub(" ", out)
            out = BITRATE_RE.sub(" ", out)
            out = BRACKET_JUNK_RE.sub(" ", out)
        out = EMPTY_PAIR_RE.sub(" ", out)
        if out == prev:
            break
    return MULTISPACE_RE.sub(" ", out).strip(" \t\r\n-–—_|,:؛،.~")


# --------------------------------------------------------------------------- #
# 2) Filename -> (artist, title)   (requirement #1)
# --------------------------------------------------------------------------- #
TRACKNO_RE   = re.compile(r"^\s*\d{1,3}[\s.\-_]+(?=\S)")
SEPARATOR_RE = re.compile(r"\s[-–—]\s")


def parse_filename(path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    '09. Yas - Rap Game (320kbps).mp3' -> ('Yas', 'Rap Game')
    'Yas_-_Rap_Game.mp3'               -> ('Yas', 'Rap Game')
    'Rap Game.mp3'                     -> (None, 'Rap Game')
    '99 Problems.mp3'                  -> (None, '99 Problems')   <- doesn't break
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = stem.replace("_", " ")
    stem = clean_text(stem, deep=True)
    if not stem:
        return None, None

    if SEPARATOR_RE.search(stem):                       # pattern "Artist - Title"
        stem = TRACKNO_RE.sub("", stem)                 # remove track number "01. "
        parts = SEPARATOR_RE.split(stem, maxsplit=1)
        artist, title = parts[0].strip(), parts[1].strip()
        if artist and title:
            return artist, title
    else:
        stem = re.sub(r"^\s*\d{1,3}\.\s+", "", stem)    # "05. Song" -> "Song"

    return None, stem.strip()


# --------------------------------------------------------------------------- #
# 3) Song model
# --------------------------------------------------------------------------- #
@dataclass
class Song:
    artist: str
    title: str
    album: Optional[str] = None
    cover: Optional[str] = None            # cover_xl (~1000x1000)
    cover_medium: Optional[str] = None     # fallback 500x500
    release_date: Optional[str] = None
    duration: Optional[int] = None         # seconds
    track_position: Optional[int] = None
    disk_number: Optional[int] = None
    isrc: Optional[str] = None
    genre: Optional[str] = None
    bpm: Optional[float] = None
    gain: Optional[float] = None
    preview: Optional[str] = None          # 30-second preview
    deezer_link: Optional[str] = None


# --------------------------------------------------------------------------- #
# 4) Deezer   (requirement #2 and #4 — fixed version + your TODO resolved)
# --------------------------------------------------------------------------- #
def _http_get(url: str, params: Optional[dict] = None) -> dict:
    last: Optional[Exception] = None
    for attempt in range(HTTP_RETRIES + 1):
        try:
            r = requests.get(url, params=params or {}, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("error"):
                raise ValueError(f"Deezer API error: {data['error']}")
            return data
        except Exception as exc:
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise last  # type: ignore[misc]


def _norm(s: str) -> str:
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)       # RapGame -> Rap Game
    return re.sub(r"[^\w\s]", " ", s.lower(), flags=re.UNICODE)


def _pick_best(results: List[dict], query: str, min_score: float) -> dict:
    """Best result based on word overlap with query (+ Deezer popularity as tie-breaker)."""
    q_tokens = set(_norm(clean_text(query)).split())
    if not q_tokens:
        return results[0]

    best, best_key = results[0], -1.0
    for tr in results:
        hay = _norm(f"{(tr.get('artist') or {}).get('name', '')} {tr.get('title', '')}")
        overlap = len(q_tokens & set(hay.split())) / len(q_tokens)
        key = overlap + (tr.get("rank", 0) / 1_000_000) / 1000
        if key > best_key:
            best, best_key = tr, key

    hay = _norm(f"{(best.get('artist') or {}).get('name', '')} {best.get('title', '')}")
    overlap = len(q_tokens & set(hay.split())) / len(q_tokens)
    if min_score and overlap < min_score:
        raise LookupError(f"weak match (score={overlap:.2f} < {min_score})")
    return best


def _build_full_song(track: dict) -> Song:
    """Search result -> complete metadata (with /track/{id} and /album/{id})."""
    try:
        full = _http_get(f"{DEEZER_API}/track/{track['id']}")
    except Exception:
        full = track
    album = full.get("album") or {}

    genre = None
    try:
        al = _http_get(f"{DEEZER_API}/album/{album['id']}")
        g = (al.get("genres") or {}).get("data") or []
        if g:
            genre = g[0]["name"]
    except Exception:
        pass

    return Song(
        artist         = (full.get("artist") or {}).get("name") or track["artist"]["name"],
        title          = full.get("title") or track["title"],
        album          = album.get("title"),
        cover          = album.get("cover_xl") or (track.get("album") or {}).get("cover_xl"),
        cover_medium   = album.get("cover_medium") or (track.get("album") or {}).get("cover_medium"),
        release_date   = album.get("release_date"),
        duration       = full.get("duration"),
        track_position = full.get("track_position"),
        disk_number    = full.get("disk_number"),
        isrc           = full.get("isrc"),
        bpm            = full.get("bpm"),
        gain           = full.get("gain"),
        preview        = full.get("preview"),
        deezer_link    = full.get("link") or track.get("link"),
        genre          = genre,
    )


def song_meta(query: str, listed: bool = False,
              min_score: Optional[float] = None) -> Union[Song, List[Song]]:
    """
    Search Deezer for a song and return its metadata (fixed version).

    Args:
        query:     any string that works for search ("Artist - Title", "Title", Persian...)
        listed:    True  -> list[Song] (artist, title, album, cover) from all results
                   False -> best result with complete metadata
        min_score: override matching strictness for this call

    Raises:
        LookupError when no acceptable result is found.
    """
    if min_score is None:
        min_score = MIN_MATCH_SCORE

    query = clean_text(query, deep=True) or query     # don't send spammy query!
    data = _http_get(f"{DEEZER_API}/search",
                     {"q": query, "limit": 25 if listed else 10})
    results = data.get("data") or []
    if not results:
        raise LookupError(f"No songs found for query: {query!r}")

    if listed:
        return [Song(artist=t["artist"]["name"],
                     title=t["title"],
                     album=(t.get("album") or {}).get("title"),
                     cover=(t.get("album") or {}).get("cover_xl"))
                for t in results]

    return _build_full_song(_pick_best(results, query, min_score))


def itunes_fallback(query: str) -> Optional[Song]:
    """Fallback when Deezer finds nothing (especially for Persian-language music)."""
    try:
        r = requests.get("https://itunes.apple.com/search",
                         params={"term": query, "media": "music", "limit": 1},
                         timeout=HTTP_TIMEOUT)
        res = (r.json() or {}).get("results") or []
        if not res:
            return None
        t = res[0]
        return Song(
            artist=t.get("artistName"), title=t.get("trackName"),
            album=t.get("collectionName"),
            cover=(t.get("artworkUrl100") or "").replace("100x100", "1000x1000"),
            release_date=(t.get("releaseDate") or "")[:10],
            genre=t.get("primaryGenreName"),
            track_position=t.get("trackNumber"),
        )
    except Exception:
        return None


def download_cover(song: Song) -> Optional[bytes]:
    """Download original cover; try cover_xl first, then cover_medium."""
    for url in (song.cover, song.cover_medium):
        if not url:
            continue
        try:
            r = requests.get(url, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            if r.content:
                return r.content
        except Exception:
            continue
    return None


def _guess_mime(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


# --------------------------------------------------------------------------- #
# Lyrics — if you have your own lyrics function, plug it in here
# --------------------------------------------------------------------------- #
def fetch_lyrics(song: Song) -> Optional[str]:
    """Default: lrclib.net (free, no key). Returns plain text or LRC or None."""
    headers = {"User-Agent": "MusicLyricsEmbedder/1.0 (github.com/Dani-Devlop)"}
    try:
        params = {"artist_name": song.artist, "track_name": song.title}
        if song.album:
            params["album_name"] = song.album
        if song.duration:
            params["duration"] = song.duration
        r = requests.get(f"{LRCLIB_API}/get", params=params,
                         timeout=HTTP_TIMEOUT, headers=headers)
        if r.ok:
            d = r.json()
            if d.get("syncedLyrics") or d.get("plainLyrics"):
                return d.get("syncedLyrics") or d.get("plainLyrics")
    except Exception:
        pass
    try:
        r = requests.get(f"{LRCLIB_API}/search",
                         params={"track_name": song.title, "artist_name": song.artist},
                         timeout=HTTP_TIMEOUT, headers=headers)
        for item in (r.json() or [])[:3]:
            if item.get("syncedLyrics") or item.get("plainLyrics"):
                return item.get("syncedLyrics") or item.get("plainLyrics")
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- #
# 5) Reading / writing tags
# --------------------------------------------------------------------------- #
def read_file_tags(path: str) -> dict:
    info = {"title": None, "artist": None, "album": None,
            "has_cover": False, "has_lyrics": False}
    ext = os.path.splitext(path)[1].lower()

    if ext == ".mp3":
        try:
            tags = ID3(path)
        except Exception:
            return info
        info["title"]  = str(tags.get("TIT2")) if tags.get("TIT2") else None
        info["artist"] = str(tags.get("TPE1")) if tags.get("TPE1") else None
        info["album"]  = str(tags.get("TALB")) if tags.get("TALB") else None
        info["has_cover"]  = any(k.startswith("APIC") for k in tags.keys())
        info["has_lyrics"] = any(k.startswith(("USLT", "SYLT")) for k in tags.keys())
    elif ext == ".flac":
        try:
            fl = FLAC(path)
        except Exception:
            return info
        info["title"]  = (fl.get("title") or [None])[0]
        info["artist"] = (fl.get("artist") or [None])[0]
        info["album"]  = (fl.get("album") or [None])[0]
        info["has_cover"]  = len(fl.pictures) > 0
        info["has_lyrics"] = any(k.lower() == "lyrics" for k in fl.keys())
    return info


def write_mp3(path: str, song: Song, lyrics: Optional[str], *,
              had_cover: bool, had_lyrics: bool, update_cover: bool,
              overwrite_lyrics: bool, skip_covers: bool) -> dict:
    changed = {"cover": False, "lyrics": False, "tags": False}
    try:
        tags = ID3(path)
    except Exception:
        tags = ID3()

    S = lambda x: clean_text(x) if isinstance(x, str) else x   # noqa: E731

    tags.setall("TIT2", [TIT2(encoding=3, text=S(song.title))])
    tags.setall("TPE1", [TPE1(encoding=3, text=S(song.artist))])
    if song.album:
        tags.setall("TALB", [TALB(encoding=3, text=S(song.album))])
    if song.genre:
        tags.setall("TCON", [TCON(encoding=3, text=S(song.genre))])
    if song.track_position:
        tags.setall("TRCK", [TRCK(encoding=3, text=str(song.track_position))])
    if song.disk_number:
        tags.setall("TPOS", [TPOS(encoding=3, text=str(song.disk_number))])
    if song.release_date:
        tags.setall("TDRC", [TDRC(encoding=3, text=str(song.release_date))])
    if song.isrc:
        tags.setall("TSRC", [TSRC(encoding=3, text=song.isrc)])
    if song.bpm:
        tags.setall("TBPM", [TBPM(encoding=3, text=str(int(round(song.bpm))))])
    if song.duration:
        tags.setall("TLEN", [TLEN(encoding=3, text=str(song.duration * 1000))])
    changed["tags"] = True

    if not tags.getall("COMM"):
        tags.add(COMM(encoding=3, lang="eng", desc="source",
                      text=f"Metadata via Deezer {song.deezer_link or ''}".strip()))

    # --- Cover: only if missing (or with --update-cover always) ---
    if not skip_covers and (update_cover or not had_cover):
        img = download_cover(song)
        if img:
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime=_guess_mime(img), type=3,
                          desc="Cover", data=img))
            changed["cover"] = True

    if lyrics and (overwrite_lyrics or not had_lyrics):
        tags.setall("USLT", [USLT(encoding=3, lang="eng", desc="Lyrics", text=lyrics)])
        changed["lyrics"] = True

    tags.save(path)   # if your player wants ID3v2.3: tags.save(path, v2_version=3)
    return changed


def write_flac(path: str, song: Song, lyrics: Optional[str], *,
               had_cover: bool, had_lyrics: bool, update_cover: bool,
               overwrite_lyrics: bool, skip_covers: bool) -> dict:
    changed = {"cover": False, "lyrics": False, "tags": False}
    fl = FLAC(path)
    S = lambda x: clean_text(x) if isinstance(x, str) else x   # noqa: E731

    fl["title"]  = S(song.title)
    fl["artist"] = S(song.artist)
    if song.album:          fl["album"]       = S(song.album)
    if song.genre:          fl["genre"]       = S(song.genre)
    if song.release_date:   fl["date"]        = str(song.release_date)
    if song.track_position: fl["tracknumber"] = str(song.track_position)
    if song.disk_number:    fl["discnumber"]  = str(song.disk_number)
    if song.isrc:           fl["isrc"]        = song.isrc
    if song.bpm:            fl["bpm"]         = str(int(round(song.bpm)))
    changed["tags"] = True

    if not skip_covers and (update_cover or not had_cover):
        img = download_cover(song)
        if img:
            pic = Picture()
            pic.type, pic.mime, pic.desc = 3, _guess_mime(img), "Cover"
            pic.data = img
            fl.clear_pictures()
            fl.add_picture(pic)
            changed["cover"] = True

    if lyrics and (overwrite_lyrics or not had_lyrics):
        fl["lyrics"] = lyrics
        changed["lyrics"] = True

    fl.save()
    return changed


# --------------------------------------------------------------------------- #
# 6) Pipeline
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    path: str
    ok: bool
    reason: str = ""
    song: Optional[Song] = None
    cover_added: bool = False
    lyrics_added: bool = False


def build_queries(tags: dict, path: str) -> List[str]:
    """Search candidates: first cleaned tags, then filename."""
    fn_artist, fn_title = parse_filename(path)
    t_title  = clean_text(tags.get("title") or "", deep=True)
    t_artist = clean_text(tags.get("artist") or "", deep=True)

    q = []
    if t_artist and t_title:
        q += [f"{t_artist} {t_title}", f"{t_title} {t_artist}"]
    if fn_artist and fn_title:
        q += [f"{fn_artist} {fn_title}", f"{fn_title} {fn_artist}"]
    if t_title:
        q.append(t_title)
    if fn_title:
        q.append(fn_title)
    if t_artist:
        q.append(t_artist)

    return list(dict.fromkeys(x for x in q if x and len(x.strip()) > 1))


def process_file(path: str, *, dry_run: bool = False, update_cover: bool = False,
                 overwrite_lyrics: bool = False, skip_covers: bool = False,
                 skip_lyrics: bool = False) -> Result:
    """Complete pipeline for a single audio file."""
    tags = read_file_tags(path)
    queries = build_queries(tags, path)
    if not queries:
        return Result(path, ok=False,
                      reason="No usable name found in tags or filename")

    song: Optional[Song] = None
    for query in queries:                        # first query that works wins
        try:
            song = song_meta(query)
            break
        except Exception:
            continue

    if song is None:                             # iTunes fallback before giving up
        for query in queries:
            song = itunes_fallback(query)
            if song:
                break

    if song is None:
        return Result(path, ok=False,
                      reason="Not found on Deezer/iTunes | attempts: " + " ; ".join(queries))

    lyrics = None if skip_lyrics else fetch_lyrics(song)

    if not dry_run:
        writer = write_mp3 if path.lower().endswith(".mp3") else write_flac
        writer(path, song, lyrics,
               had_cover=tags["has_cover"], had_lyrics=tags["has_lyrics"],
               update_cover=update_cover, overwrite_lyrics=overwrite_lyrics,
               skip_covers=skip_covers)

    return Result(path, ok=True, song=song,
                  cover_added=(not tags["has_cover"]),
                  lyrics_added=bool(lyrics))


def collect_audio_files(root: str, recursive: bool = True) -> List[str]:
    if os.path.isfile(root):
        return [root] if os.path.splitext(root)[1].lower() in AUDIO_EXTS else []
    files: List[str] = []
    if recursive:
        for dirpath, _, names in os.walk(root):
            files += [os.path.join(dirpath, n) for n in names
                      if os.path.splitext(n)[1].lower() in AUDIO_EXTS]
    else:
        files = [os.path.join(root, n) for n in os.listdir(root)
                 if os.path.splitext(n)[1].lower() in AUDIO_EXTS
                 and os.path.isfile(os.path.join(root, n))]
    return sorted(files)


def process_folder(root: str, *, recursive: bool = True, dry_run: bool = False,
                   update_cover: bool = False, overwrite_lyrics: bool = False,
                   skip_covers: bool = False, skip_lyrics: bool = False):
    files = collect_audio_files(root, recursive)
    print(f"🎵 {len(files)} audio file(s) found\n")

    done, failed = [], []
    for i, path in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {os.path.basename(path)}")
        try:
            res = process_file(path, dry_run=dry_run, update_cover=update_cover,
                               overwrite_lyrics=overwrite_lyrics,
                               skip_covers=skip_covers, skip_lyrics=skip_lyrics)
        except Exception as exc:
            res = Result(path, ok=False, reason=f"Error: {exc}")

        if res.ok:
            s, extra = res.song, []
            if res.cover_added:  extra.append("cover")
            if res.lyrics_added: extra.append("lyrics")
            print(f"    ✅ {s.artist} — {s.title}"
                  + (f"  [{s.album}]" if s.album else "")
                  + ("  (+" + ", ".join(extra) + ")" if extra else ""))
            done.append(res)
        else:
            print(f"    ❌ Failed → {res.reason}")
            failed.append(res)

        time.sleep(SLEEP_BETWEEN_FILES)

    print("\n" + "=" * 62)
    print(f"✅ Successful: {len(done)}    ❌ Failed: {len(failed)}")

    if failed:
        print("\n❌ These files could not be identified/updated:")
        for r in failed:
            print(f"   • {r.path}\n     └─ {r.reason}")
        try:
            with open(FAILED_REPORT, "w", encoding="utf-8") as fh:
                for r in failed:
                    fh.write(f"{r.path}\t{r.reason}\n")
            print(f"\n📝 List saved: {FAILED_REPORT}")
        except OSError:
            pass
    return done, failed


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Music metadata + original cover + lyrics embedder (Deezer)")
    ap.add_argument("path", help="An audio file or a folder")
    ap.add_argument("-r", "--recursive", action="store_true", help="Scan subfolders too")
    ap.add_argument("--dry-run", action="store_true", help="Only show, don't write anything")
    ap.add_argument("--update-cover", action="store_true",
                    help="Replace existing covers with original ones too")
    ap.add_argument("--no-covers", action="store_true", help="Don't touch covers at all")
    ap.add_argument("--overwrite-lyrics", action="store_true", help="Overwrite existing lyrics")
    ap.add_argument("--skip-lyrics", action="store_true", help="Don't fetch lyrics")
    ap.add_argument("--min-score", type=float, default=MIN_MATCH_SCORE,
                    help="0..1 matching strictness (0 = always take best guess)")
    args = ap.parse_args()

    global MIN_MATCH_SCORE
    MIN_MATCH_SCORE = args.min_score

    process_folder(args.path, recursive=args.recursive, dry_run=args.dry_run,
                   update_cover=args.update_cover,
                   overwrite_lyrics=args.overwrite_lyrics,
                   skip_covers=args.no_covers, skip_lyrics=args.skip_lyrics)


if __name__ == "__main__":
    main()
