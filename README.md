<div align="center">

# 🎵 Music Lyrics Embedder

**Automatically embed lyrics, original album covers & full metadata into your music library.**

[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/Dani-Devlop/Music_Lyrics_Embedder/pulls)
[![Code style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

**No API keys required** · Deezer + iTunes + LRCLIB · MP3 & FLAC

</div>

---

## 📖 Table of Contents

- [Features](#-features)
- [What's New](#-whats-new-in-v20)
- [How It Works](#-how-it-works)
- [Installation](#-installation)
- [Quick Start](#-quick-start)
- [CLI Reference](#%EF%B8%8F-cli-reference)
- [Use as a Library](#-use-as-a-library)
- [Spam Cleaning](#-spam-cleaning)
- [Embedded Metadata](#-embedded-metadata)
- [Configuration](#%EF%B8%8F-configuration)
- [Sample Output](#-sample-output)
- [FAQ / Troubleshooting](#-faq--troubleshooting)
- [Roadmap](#%EF%B8%8F-roadmap)
- [Contributing](#-contributing)
- [License](#-license)
- [Acknowledgements](#-acknowledgements)

---

## ✨ Features

| | Feature | Description |
|---|---|---|
| 📝 | **Lyrics Embedding** | Fetches synced (LRC) or plain lyrics and embeds them directly into the audio file |
| 🏷️ | **Smart Filename Parsing** | Extracts `Artist — Title` from messy filenames like `01. Yas - Rap Game (320kbps).mp3` |
| 🖼️ | **Original Album Covers** | Downloads official **1000×1000** covers (`cover_xl`) and embeds them — automatically adds one if the file has none |
| 🧹 | **Spam Cleaner** | Strips `@handles`, `bingo.com`, `www.bbangi.com`, `t.me/...`, `(Official Video)`, `320kbps` and more from tags & search queries |
| 🌐 | **Full Metadata Lookup** | Album, release date, genre, ISRC, BPM, track/disc number, duration & Deezer link — via `song_meta()` |
| 📋 | **Failure Report** | Files that couldn't be identified anywhere are listed at the end and saved to `failed_songs.txt` |
| 🔁 | **Batch & Recursive** | Process a single file or an entire folder tree in one command |
| 🧪 | **Dry Run** | Preview everything before writing a single byte |

---

## 🆕 What's New in v2.0

> The big metadata update — 5 new capabilities:

1. **Song name from filename** — no tags? No problem. `parse_filename()` understands
   `Artist - Title`, `01. Artist - Title`, `Yas_-_Rap_Game`, and falls back gracefully.
2. **Original covers, auto-added** — official Deezer `cover_xl` artwork is embedded
   *only when missing* (use `--update-cover` to force-replace existing covers).
3. **Spam/junk removal** — channel promos and site links inside titles are gone:

   | Before | After |
   |---|---|
   | `Rap Game @test` | `Rap Game` |
   | `Rap Game bingo.com` | `Rap Game` |
   | `Rap Game www.bbangi.com` | `Rap Game` |
   | `Track (Official Video) (320kbps)` | `Track` |

4. **Complete metadata via `song_meta()`** — the search engine is now fixed
   (cleaned queries + fuzzy best-match scoring) and every value is extracted properly.
5. **"I couldn't identify these" report** — anything not findable in tags, filename,
   Deezer **or** iTunes ends up in a clear final list + `failed_songs.txt`.

---

## ⚙️ How It Works

```mermaid
flowchart LR
    A[Audio file<br/>MP3 / FLAC] --> B[Read existing tags]
    B --> C["🧹 Clean spam<br/>(@ · URLs · t.me · 320kbps)"]
    C --> D{Tags usable?}
    D -- no --> E["📂 Parse filename<br/>Artist - Title"]
    D -- yes --> F[🌐 Search Deezer]
    E --> F
    F -- match --> G["📥 Full metadata<br/>/track/{id} · /album/{id}"]
    F -- no match --> H[🍎 iTunes fallback]
    H -- found --> G
    H -- nothing --> X["📋 failed_songs.txt"]
    G --> I[🖼️ Download cover_xl]
    G --> J[📝 Fetch lyrics · lrclib.net]
    I --> K["✍️ Write tags<br/>ID3 / Vorbis"]
    J --> K
    K --> L[✅ Done]
