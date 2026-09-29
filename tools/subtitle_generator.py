"""Generate WebVTT subtitle files from transcript JSONs.

Reads each transcript JSON in static/txt/, produces a .vtt file in static/subtitles/
that the video player can load as a <track> element.

Usage:
    python subtitle_generator.py                              # generate all
    python subtitle_generator.py "static/txt/xxx_en.json"     # generate one
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEXT_DIR = REPO / "static" / "txt"
SUBTITLE_DIR = REPO / "static" / "subtitles"


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm for WebVTT."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def json_to_vtt(transcript_path: Path) -> str:
    """Convert a transcript JSON to WebVTT format string."""
    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    segments = data.get("segments", [])

    lines = ["WEBVTT", ""]
    for i, seg in enumerate(segments, 1):
        start = format_timestamp(seg["start"])
        end = format_timestamp(seg["end"])
        text = seg["text"].strip()
        if not text:
            continue
        lines.append(str(i))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")

    return "\n".join(lines)


def output_path_for(transcript_path: Path) -> Path:
    """Derive the VTT output path from the transcript path."""
    stem = transcript_path.stem
    return SUBTITLE_DIR / f"{stem}.vtt"


def main() -> None:
    import argparse

    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Generate WebVTT subtitles from transcript JSONs")
    parser.add_argument("transcript", nargs="?", help="Path to a transcript JSON (default: all in static/txt)")
    args = parser.parse_args()

    SUBTITLE_DIR.mkdir(parents=True, exist_ok=True)

    if args.transcript:
        targets = [Path(args.transcript)]
    else:
        targets = sorted(
            p for p in TEXT_DIR.glob("*.json")
            if "_analysis" not in p.name and "_kg" not in p.name
        )

    if not targets:
        print("No transcript JSON files found.")
        sys.exit(1)

    print(f"Generating subtitles for {len(targets)} transcript(s)...\n")

    for i, tpath in enumerate(targets, 1):
        out = output_path_for(tpath)
        try:
            vtt_content = json_to_vtt(tpath)
            out.write_text(vtt_content, encoding="utf-8")
            line_count = vtt_content.count("-->")
            print(f"  [{i}/{len(targets)}] {tpath.name} -> {out.name} ({line_count} cues)")
        except Exception as exc:
            print(f"  [{i}/{len(targets)}] FAILED: {tpath.name} — {exc}")

    print(f"\nDone. Subtitles saved to {SUBTITLE_DIR}/")


if __name__ == "__main__":
    main()
