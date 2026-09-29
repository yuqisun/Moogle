# Moogle - Video Content Locator

Search any moment across all videos. Ask questions or enter keywords,
and Moogle searches across all video transcripts in the library, generates an AI-powered
response via LLM, and provides timestamped reference links that jump directly
to the relevant moment in the video player.

## Directory Structure

```
moogle/
├── streamlit_app.py              # Streamlit chat frontend (port 8501)
├── video_locator.py              # Core: text -> timestamp + BM25 search
├── smoke_test.py                 # AppTest headless regression test
├── run.ps1                       # One-click launcher (Streamlit + static server)
├── .env                          # Configuration (LLM, search params, Neo4j)
├── .env.example                  # Configuration template
├── requirements.txt              # Python dependencies
├── tools/                        # Offline processing scripts
│   ├── transcribe_video.py       #   Whisper transcription (large-v3-turbo, CPU)
│   ├── video_analyzer.py         #   LLM analysis (summary, chapters, topics)
│   ├── kg_extractor.py           #   LLM knowledge graph extraction (triples)
│   └── subtitle_generator.py     #   Transcript JSON -> WebVTT subtitles
├── tests/                        # Search quality evaluation
│   ├── eval_search.py            #   P@K / R@K / MRR metrics
│   └── search_queries.json       #   Annotated test queries (ground truth)

├── .venv/                        # Python 3.11.9 virtual environment (Streamlit)
└── static/
    ├── video/                    # Video files (.mp4)
    ├── txt/                      # Transcript JSONs + analysis + KG results
    ├── subtitles/                # WebVTT subtitle files
    └── player/                   # Video player page (HTML/CSS/JS)
        ├── player.html           #   Player + About + Knowledge Graph tabs
        └── css/ js/ img/ video/  #   Template assets
```

## Quick Start

```powershell
cd D:\workspace\moogle

# One-click launch (recommended)
.\run.ps1

# Or start separately:
# Terminal 1 - Static file server (video player, port 8502)
.venv\Scripts\python.exe -m http.server 8502 --bind 127.0.0.1 --directory static

# Terminal 2 - Streamlit chat (port 8501)
.venv\Scripts\streamlit.exe run streamlit_app.py
```

Open **http://127.0.0.1:8501** for the chat interface.
Video player pages open at **http://127.0.0.1:8502/player/player.html?src=...&t=...**

## Tools Usage

All tool scripts live in `tools/`. Transcription uses the Anaconda py39 environment
(because it has torch + whisper installed); the rest use C:\Python311.

### 1. Transcribe a video

Convert video audio to timestamped transcript JSON using Whisper large-v3-turbo (CPU).

```powershell
# Transcribe the default video in static/video/
C:\ProgramData\Anaconda3\envs\py39\python.exe tools\transcribe_video.py

# Transcribe a specific video
C:\ProgramData\Anaconda3\envs\py39\python.exe tools\transcribe_video.py "static\video\xxx.mp4"

# Specify language (default: auto-detect)
C:\ProgramData\Anaconda3\envs\py39\python.exe tools\transcribe_video.py "static\video\xxx.mp4" --language zh

# List existing transcripts
C:\ProgramData\Anaconda3\envs\py39\python.exe tools\transcribe_video.py --list
```

Output: `static/txt/<video_name>_en.json` (or without `_en` for non-English)

CPU inference ~0.9-1.16x realtime (6 logical cores). A 7-minute video takes ~8 minutes.

### 2. Analyze a video (LLM)

Extract summary, speaker, chapters, topics, audience, prerequisites, and key takeaways
using the LLM configured in `.env`.

```powershell
# Analyze all transcripts
C:\Python311\python.exe tools\video_analyzer.py

# Analyze one file
C:\Python311\python.exe tools\video_analyzer.py "static\txt\xxx_en.json"

# Force re-analyze (overwrite existing)
C:\Python311\python.exe tools\video_analyzer.py --force
```

Output: `static/txt/<video_name>_en_analysis.json`

The player page's **About** tab automatically loads this file and displays:
- Summary, Speaker, Audience
- Key Topics (tag chips)
- Chapters (clickable timeline - jumps to Player tab at chapter start time)
- Prerequisites, Key Takeaways

### 3. Extract knowledge graph (LLM)

Extract entity-relation-entity triples from the transcript using LLM.

```powershell
# Extract from all transcripts
C:\Python311\python.exe tools\kg_extractor.py

# Extract from one file
C:\Python311\python.exe tools\kg_extractor.py "static\txt\xxx_en.json"

# Force re-extract
C:\Python311\python.exe tools\kg_extractor.py --force
```

Output: `static/txt/<video_name>_en_kg.json` (entities + triples)

If `.env` has Neo4j configured, triples are also written to Neo4j:
```ini
NEO4J_URI=http://localhost:7474
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
```

The player page's **Knowledge Graph** tab renders the graph using vis.js
(nodes colored by type: concept/method/application/property/example).

### 4. Generate subtitles

Convert transcript JSON to WebVTT subtitle files for the video player.

```powershell
# Generate for all transcripts
C:\Python311\python.exe tools\subtitle_generator.py

# Generate for one file
C:\Python311\python.exe tools\subtitle_generator.py "static\txt\xxx_en.json"
```

Output: `static/subtitles/<video_name>_en.vtt`

Subtitles are loaded automatically by the player and enabled by default.
Toggle via the video player's native CC menu (three-dot menu -> Captions).

### Typical workflow for a new video

```powershell
# Step 1: Transcribe
C:\ProgramData\Anaconda3\envs\py39\python.exe tools\transcribe_video.py "static\video\new_video.mp4"

# Step 2: Generate subtitles
C:\Python311\python.exe tools\subtitle_generator.py

# Step 3: Analyze with LLM (populates About page)
C:\Python311\python.exe tools\video_analyzer.py

# Step 4: Extract knowledge graph (populates Knowledge Graph page)
C:\Python311\python.exe tools\kg_extractor.py
```

## Configuration (.env)

```ini
# Search parameters (also adjustable via sidebar sliders at runtime)
SEARCH_TOP_K=10          # Maximum results to return
SEARCH_MIN_SCORE=0.3     # Minimum relevance score (0~1)

# LLM (any OpenAI-compatible API)
LLM_API_KEY=             # Leave empty for template replies (no API call)
LLM_API_BASE=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# Static file server port
STATIC_PORT=8502

# Neo4j (optional - for knowledge graph persistence)
NEO4J_URI=
NEO4J_USER=neo4j
NEO4J_PASSWORD=
```

## Search Algorithm

`video_locator.py` implements a multi-stage search:

1. **Tokenization** - extract English words, lowercase
2. **Stopword removal** - filter out "the/a/is/are/to/of/..." (50+ words)
3. **Stemming** - networks->network, training->train, predicts->predict
4. **BM25 scoring** - term frequency x inverse document frequency x length normalization (k1=1.2, b=0.5)
5. **Phrase matching boost** - exact substring +0.50, normalized match +0.35, bigram match +0.25
6. **Global ranking** - sort by score descending across all transcripts, return top-K

Quality is measured by `tests/eval_search.py` (18 annotated queries):
```powershell
.venv\Scripts\python.exe tests\eval_search.py --top-k 10
```

## Timestamp Locator

`video_locator.py`'s `locate()` method maps text to video timestamps:

1. `text.find(fragment)` gets the character offset in the full text
2. Accumulate segment texts until the running length reaches that offset
3. The matching segment's `start` is the video timestamp
4. Normalized fallback: ignores whitespace and case when exact match fails
5. `find()` returning -1 explicitly reports `not_found` (never silently jumps to 0s)

CLI usage:
```powershell
.venv\Scripts\python.exe video_locator.py "Let's start with a housing price prediction example."
.venv\Scripts\python.exe video_locator.py --all "neural network"
```

## Verification

```powershell
.venv\Scripts\python.exe smoke_test.py
```
