"""Video transcript analyzer — uses LLM to extract structured metadata from a transcript JSON.

Reads a transcript JSON (produced by transcribe_video.py), sends the full text to the LLM
configured in .env, and writes a structured analysis JSON alongside it.

Usage:
    python video_analyzer.py                              # analyze all transcripts
    python video_analyzer.py "static/txt/xxx_en.json"     # analyze one file
    python video_analyzer.py --force                      # re-analyze even if output exists

The LLM config (API key, base URL, model) is read from .env — same as streamlit_app.py.
Switch to any OpenAI-compatible API by editing .env only.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEXT_DIR = REPO / "static" / "txt"


# ---------------------------------------------------------------- Config (same as streamlit_app.py)

def load_env(env_path: Path = REPO / ".env") -> dict[str, str]:
    config: dict[str, str] = {}
    if not env_path.exists():
        return config
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip().strip('"').strip("'")
    return config


CFG = load_env()
LLM_API_KEY = CFG.get("LLM_API_KEY", "")
LLM_API_BASE = CFG.get("LLM_API_BASE", "https://api.deepseek.com/v1")
LLM_MODEL = CFG.get("LLM_MODEL", "deepseek-chat")


# ---------------------------------------------------------------- LLM call

def call_llm(prompt: str, *, max_tokens: int = 2048, temperature: float = 0.3) -> str:
    """Call the LLM configured in .env. Returns the assistant's reply text."""
    if not LLM_API_KEY:
        raise SystemExit("LLM_API_KEY is not set in .env — cannot call LLM.")

    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{LLM_API_BASE.rstrip('/')}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------- Prompt

ANALYSIS_PROMPT = """\
You are analyzing a video transcript to produce structured metadata for a video player page.
The transcript is from a lecture / presentation video.

Based on the transcript below, produce a JSON object with EXACTLY these fields:

{
  "summary": "A 2-3 paragraph summary of the video content. Be specific about what is taught/discussed.",
  "speaker": {
    "name": "Speaker name if mentioned, otherwise 'Unknown'",
    "bio": "One sentence about the speaker based on context clues (affiliation, expertise). Use 'No information available' if nothing can be inferred."
  },
  "topics": ["topic1", "topic2", ...],
  "chapters": [
    {
      "title": "Chapter title",
      "start_seconds": 0.0,
      "end_seconds": 123.0,
      "description": "One sentence describing what this chapter covers."
    }
  ],
  "audience": "Who is this video for? (e.g., beginners in machine learning, financial analysts, etc.)",
  "prerequisites": ["prerequisite1", "prerequisite2", ...],
  "key_takeaways": ["takeaway1", "takeaway2", ...]
}

Rules:
- "topics": 5-15 specific technical/business terms or concepts mentioned in the video.
- "chapters": Divide the video into 3-8 logical chapters based on topic shifts. Use the transcript timestamps to determine start_seconds and end_seconds. The first chapter starts at 0.0 and the last chapter ends at the transcript's final timestamp.
- "prerequisites": 0-5 items. What background knowledge does the viewer need? Empty array if none required.
- "key_takeaways": 3-7 bullet points of the most important things a viewer would learn.
- Output ONLY valid JSON, no markdown fences, no extra text.

--- TRANSCRIPT START ---

{transcript}

--- TRANSCRIPT END ---
"""


# ---------------------------------------------------------------- Analysis

def analyze_transcript(transcript_path: Path) -> dict:
    """Run LLM analysis on a transcript JSON and return the structured result."""
    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    full_text = data.get("text", "")
    segments = data.get("segments", [])

    if not full_text and segments:
        full_text = "".join(seg["text"] for seg in segments)

    if not full_text.strip():
        raise ValueError(f"Transcript is empty: {transcript_path.name}")

    # Build the prompt with transcript text + segment timestamps for chapter detection
    # Include a compact timestamp index so the LLM can reference timestamps
    timestamp_index = []
    for seg in segments:
        timestamp_index.append(f"[{seg['start']:.1f}s] {seg['text'].strip()}")

    # If transcript is very long, include full text but cap at ~30k chars
    transcript_for_prompt = full_text
    if len(transcript_for_prompt) > 30000:
        transcript_for_prompt = transcript_for_prompt[:30000] + "\n... (truncated)"

    # Append timestamp index (compact) for chapter boundary detection
    ts_block = "\n".join(timestamp_index)
    if len(ts_block) > 15000:
        # Sample every Nth segment to stay within limits
        step = max(1, len(timestamp_index) // 200)
        ts_block = "\n".join(timestamp_index[::step])

    combined = f"{transcript_for_prompt}\n\n--- TIMESTAMP INDEX ---\n{ts_block}"

    prompt = ANALYSIS_PROMPT.replace("{transcript}", combined)

    print(f"  Calling LLM ({LLM_MODEL}) ...", flush=True)
    started = time.time()
    raw_reply = call_llm(prompt, max_tokens=2048, temperature=0.3)
    elapsed = time.time() - started
    print(f"  LLM replied in {elapsed:.1f}s ({len(raw_reply)} chars)", flush=True)

    # Parse JSON from the reply (handle potential markdown fences)
    cleaned = raw_reply.strip()
    if cleaned.startswith("```"):
        # Remove ```json ... ```
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find JSON block in the reply
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(cleaned[start:end])
        else:
            raise ValueError(f"LLM reply is not valid JSON:\n{raw_reply[:500]}")

    # Attach source info
    result["_source"] = transcript_path.name
    result["_model"] = LLM_MODEL
    result["_analyzed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    result["_duration_seconds"] = segments[-1]["end"] if segments else 0.0

    return result


def output_path_for(transcript_path: Path) -> Path:
    """Derive the analysis output path from the transcript path."""
    stem = transcript_path.stem
    # e.g. "xxx_en.json" -> "xxx_en_analysis.json"
    return transcript_path.parent / f"{stem}_analysis.json"


def main() -> None:
    import argparse

    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Analyze video transcripts with LLM")
    parser.add_argument("transcript", nargs="?", help="Path to a transcript JSON (default: all in static/txt)")
    parser.add_argument("--force", action="store_true", help="Re-analyze even if output already exists")
    args = parser.parse_args()

    if not LLM_API_KEY:
        print("ERROR: LLM_API_KEY is not set in .env")
        sys.exit(1)

    print(f"LLM: {LLM_MODEL} @ {LLM_API_BASE}")

    if args.transcript:
        targets = [Path(args.transcript)]
    else:
        targets = sorted(TEXT_DIR.glob("*_en.json")) + sorted(
            p for p in TEXT_DIR.glob("*.json")
            if "_analysis" not in p.name and "_en" not in p.stem
        )
        # Deduplicate
        seen: set[str] = set()
        unique = []
        for t in targets:
            if t.name not in seen:
                seen.add(t.name)
                unique.append(t)
        targets = unique

    if not targets:
        print("No transcript JSON files found.")
        sys.exit(1)

    print(f"Found {len(targets)} transcript(s) to analyze.\n")

    for i, tpath in enumerate(targets, 1):
        out = output_path_for(tpath)
        if out.exists() and not args.force:
            print(f"[{i}/{len(targets)}] SKIP (exists): {out.name}")
            continue

        print(f"[{i}/{len(targets)}] Analyzing: {tpath.name}")
        try:
            result = analyze_transcript(tpath)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  -> Saved: {out.name} ({out.stat().st_size / 1024:.1f} KB)")
        except Exception as exc:
            print(f"  -> FAILED: {exc}")

    print("\nDone.")


if __name__ == "__main__":
    main()
