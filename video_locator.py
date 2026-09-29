"""Text-to-video timestamp locator.

Core logic extracted from app.py's get_video_ts(), hardened and extended.

Principle (character-offset alignment):
    1. The knowledge base returns text fragments without timestamps;
    2. The transcript JSON has full text and timestamped segments;
    3. text.find(fragment) gives the character offset in the full text;
    4. Accumulate segment texts until the running length reaches that offset;
       the matching segment's start time is the video timestamp.

Fixes over the original implementation:
    * find() returning -1 no longer silently yields 0.0 — returns found=False with reason.
    * Always uses joined segment text as ground truth (eliminates desync bugs).
    * Normalized fallback: ignores whitespace and case when exact match fails.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent
VIDEO_DIR = REPO / "static" / "video"
TEXT_DIR = REPO / "static" / "txt"

# ---------------------------------------------------------------- Search helpers

# English stopwords (common function words that carry little search value)
_STOPWORDS = frozenset(
    "a an the is are was were be been being have has had do does did will would "
    "shall should may might must can could of in to for on with at by from as "
    "into through during before after above below between out off over under "
    "and or but not no nor so if then than too very just about also all each "
    "every both few more most other some such only own same when where how what "
    "which who whom this that these those it its i me my we our you your he him "
    "his she her they them their there here up down".split()
)


def _stem(word: str) -> str:
    """Minimal English stemmer: strip common suffixes."""
    w = word.lower()
    if len(w) <= 3:
        return w
    # Order matters: longest suffix first
    for suffix in ("ing", "tion", "sion", "ness", "ment", "able", "ible",
                    "ous", "ive", "ful", "less", "ly",
                    "ies", "ves", "ses", "zes", "xes",
                    "ed", "er", "est", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 2:
            return w[: -len(suffix)]
    return w


def _tokenize(text: str) -> list[str]:
    """Tokenize into lowercase words, removing stopwords, applying stemming."""
    raw = re.findall(r"[a-zA-Z]+", text.lower())
    return [_stem(w) for w in raw if w not in _STOPWORDS and len(w) > 1]


def _tokenize_raw(text: str) -> list[str]:
    """Tokenize into lowercase words without filtering (for BM25 doc building)."""
    return [w.lower() for w in re.findall(r"[a-zA-Z]+", text) if len(w) > 1]

# Knowledge-base reference format (same regex as app.py)
_REF_FILE_RE = re.compile(r"] (.+?)\.txt：")
_REF_TEXT_RE = re.compile(r"\.txt：\n\n(.+?)\n\n相关度：", re.S)


@dataclass
class SearchResult:
    """A scored search result."""
    segment_index: int
    start: float
    end: float
    text: str
    score: float          # 0~1, higher = more relevant
    matched_terms: list[str]
    snippet: str           # context around the match
    video_name: str = ""   # which video this result belongs to
    transcript_path: str = ""


@dataclass(frozen=True)
class Segment:
    index: int
    start: float
    end: float
    text: str


@dataclass
class LocateResult:
    """Result of a text-to-timestamp lookup."""
    found: bool
    reason: str  # exact / normalized / not_found / empty_query
    start: float | None = None
    end: float | None = None
    segment_index: int | None = None
    matched_text: str = ""
    char_offset: int | None = None
    matched_span: tuple[int, int] | None = None

    def __str__(self) -> str:
        if not self.found:
            return f"Not found ({self.reason})"
        return (f"{self.start:.2f}s - {self.end:.2f}s  (segment {self.segment_index}, "
                f"char offset {self.char_offset}, match={self.reason})")


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Strip all whitespace and lowercase, keeping a map back to original indices."""
    chars: list[str] = []
    index_map: list[int] = []
    for i, ch in enumerate(text):
        if ch.isspace() or ch == "\u3000":
            continue
        chars.append(ch.lower())
        index_map.append(i)
    return "".join(chars), index_map


def parse_kb_reference(ref_content: str) -> tuple[str | None, str | None]:
    """Parse a knowledge-base reference string into (doc_name, cited_text)."""
    file_match = _REF_FILE_RE.search(ref_content)
    text_match = _REF_TEXT_RE.search(ref_content)
    return (
        file_match.group(1) if file_match else None,
        text_match.group(1) if text_match else None,
    )


def find_video(stem: str, video_dir: Path = VIDEO_DIR) -> Path | None:
    """Find a video file by substring match on filename."""
    if not video_dir.is_dir():
        return None
    for path in sorted(video_dir.iterdir()):
        if path.is_file() and stem in path.name and path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".avi"}:
            return path
    return None


def find_transcript(stem: str, text_dir: Path = TEXT_DIR) -> Path | None:
    """Find a transcript JSON by substring match on filename (.json only)."""
    if not text_dir.is_dir():
        return None
    for path in sorted(text_dir.glob("*.json")):
        if stem in path.name:
            return path
    return None


@dataclass
class Transcript:
    path: Path
    segments: list[Segment]
    language: str = ""
    source: str = ""
    declared_text: str = ""
    text_matches_segments: bool = True
    _offsets: list[int] = field(default_factory=list, repr=False)

    @property
    def text(self) -> str:
        """Full text, always derived from segments to guarantee offset consistency."""
        return "".join(seg.text for seg in self.segments)

    @property
    def duration(self) -> float:
        return self.segments[-1].end if self.segments else 0.0

    @property
    def video_name(self) -> str:
        """Infer the video filename from the transcript path."""
        stem = self.path.stem
        # Strip _en suffix if present
        if stem.endswith("_en"):
            stem = stem[:-3]
        return stem

    @classmethod
    def load(cls, path: Path) -> "Transcript":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        segments = [
            Segment(index=i, start=float(seg["start"]), end=float(seg["end"]), text=seg["text"])
            for i, seg in enumerate(data.get("segments", []))
        ]
        declared = data.get("text", "")
        joined = "".join(seg.text for seg in segments)

        offsets: list[int] = []
        acc = 0
        for seg in segments:
            acc += len(seg.text)
            offsets.append(acc)

        return cls(
            path=Path(path),
            segments=segments,
            language=data.get("language", ""),
            source=data.get("source", ""),
            declared_text=declared,
            text_matches_segments=(declared == joined),
            _offsets=offsets,
        )

    def segment_at(self, char_offset: int) -> Segment | None:
        """Map a character offset to the segment it falls in."""
        for seg, end_offset in zip(self.segments, self._offsets):
            if end_offset >= char_offset:
                return seg
        return self.segments[-1] if self.segments else None

    def locate(self, needle: str) -> LocateResult:
        """Locate a text fragment and return its video timestamp."""
        needle = (needle or "").strip()
        if not needle:
            return LocateResult(found=False, reason="empty_query")

        text = self.text
        offset = text.find(needle)
        reason = "exact"

        if offset < 0:
            norm_text, index_map = normalize_with_map(text)
            norm_needle, _ = normalize_with_map(needle)
            norm_offset = norm_text.find(norm_needle) if norm_needle else -1
            if norm_offset < 0:
                return LocateResult(found=False, reason="not_found")
            offset = index_map[norm_offset]
            reason = "normalized"

        seg = self.segment_at(offset)
        if seg is None:
            return LocateResult(found=False, reason="not_found")

        return LocateResult(
            found=True,
            reason=reason,
            start=seg.start,
            end=seg.end,
            segment_index=seg.index,
            matched_text=text[offset: offset + len(needle)],
            char_offset=offset,
            matched_span=(offset, offset + len(needle)),
        )

    def search(self, keyword: str) -> list[int]:
        """Simple keyword search, returns matching segment indices."""
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        norm_keyword, _ = normalize_with_map(keyword)
        hits = []
        for seg in self.segments:
            norm_seg, _ = normalize_with_map(seg.text)
            if norm_keyword and norm_keyword in norm_seg:
                hits.append(seg.index)
        return hits

    def scored_search(
        self,
        query: str,
        *,
        top_k: int = 10,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        """BM25-scored search with phrase matching boost.

        Improvements over the original hit-ratio scorer:
        1. Stopword filtering — "the/a/is" don't inflate scores
        2. Stemming — "networks" matches "network"
        3. Phrase matching boost — exact substring match gets +0.3 bonus
        4. BM25 scoring — accounts for term frequency and segment length
        """
        query = (query or "").strip()
        if not query:
            return []

        # Tokenize query (with stopwords removed + stemming)
        query_terms = _tokenize(query)
        if not query_terms:
            # All tokens were stopwords — fall back to raw tokens
            query_terms = [w.lower() for w in re.findall(r"[a-zA-Z]+", query) if len(w) > 1]
        if not query_terms:
            return []

        query_lower = query.lower()
        query_norm, _ = normalize_with_map(query)

        # Precompute BM25 parameters
        n_docs = len(self.segments)
        avg_dl = sum(len(_tokenize_raw(s.text)) for s in self.segments) / max(n_docs, 1)
        k1 = 1.2  # BM25 term saturation (lower = less saturation, more TF sensitivity)
        b = 0.5   # BM25 length normalization (lower = less length penalty)

        # Document frequency for each query term
        doc_freq: dict[str, int] = {}
        seg_tokens_cache: list[list[str]] = []
        for seg in self.segments:
            tokens = _tokenize_raw(seg.text)
            seg_tokens_cache.append(tokens)
            token_set = set(tokens)
            for qt in query_terms:
                if qt in token_set:
                    doc_freq[qt] = doc_freq.get(qt, 0) + 1

        vname = self.video_name
        tpath = str(self.path)
        results: list[SearchResult] = []

        for i, seg in enumerate(self.segments):
            seg_tokens = seg_tokens_cache[i]
            dl = len(seg_tokens)
            if dl == 0:
                continue

            # BM25 score
            bm25_score = 0.0
            matched_stems: list[str] = []
            seg_token_set = set(_tokenize(seg.text))

            for qt in query_terms:
                if qt not in seg_token_set:
                    continue
                matched_stems.append(qt)
                # Term frequency in this segment
                tf = seg_tokens.count(qt)
                # IDF (dampened — use log(1 + ...) to avoid over-penalizing common terms)
                df = doc_freq.get(qt, 0)
                idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
                # BM25 TF component
                tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg_dl))
                bm25_score += idf * tf_norm

            if bm25_score <= 0:
                continue

            # Phrase matching boost: exact substring in segment text
            phrase_bonus = 0.0
            if query_lower in seg.text.lower():
                phrase_bonus = 0.5
            elif query_norm and query_norm in normalize_with_map(seg.text)[0]:
                phrase_bonus = 0.35

            # Also check multi-word phrases from the original query
            raw_words = [w.lower() for w in re.findall(r"[a-zA-Z]+", query) if len(w) > 1]
            if len(raw_words) >= 2:
                seg_lower = seg.text.lower()
                for wi in range(len(raw_words) - 1):
                    bigram = raw_words[wi] + " " + raw_words[wi + 1]
                    if bigram in seg_lower:
                        phrase_bonus = max(phrase_bonus, 0.25)

            final_score = bm25_score + phrase_bonus

            # Normalize to 0-1 range (approximate: divide by theoretical max)
            max_possible = len(query_terms) * 3.0 + 0.3  # rough upper bound
            normalized = min(final_score / max_possible, 1.0)

            if normalized < min_score:
                continue

            lo = max(0, seg.index - 1)
            hi = min(len(self.segments), seg.index + 2)
            snippet = " ".join(self.segments[j].text.strip() for j in range(lo, hi))

            results.append(SearchResult(
                segment_index=seg.index,
                start=seg.start,
                end=seg.end,
                text=seg.text.strip(),
                score=normalized,
                matched_terms=matched_stems,
                snippet=snippet,
                video_name=vname,
                transcript_path=tpath,
            ))

        results.sort(key=lambda r: (-r.score, r.start))
        return results[:top_k]

    def snippet(self, index: int, before: int = 1, after: int = 1) -> str:
        """Get context around a segment."""
        lo = max(0, index - before)
        hi = min(len(self.segments), index + after + 1)
        return " ".join(self.segments[i].text.strip() for i in range(lo, hi))


def resolve(stem: str, video_dir: Path = VIDEO_DIR, text_dir: Path = TEXT_DIR):
    """Find both video and transcript JSON by document stem."""
    return find_video(stem, video_dir), find_transcript(stem, text_dir)


# Exclude non-transcript files from search
_EXCLUDE_PATTERNS = ("_analysis", "_kg")


def available_transcripts(text_dir: Path = TEXT_DIR) -> list[Path]:
    """Return transcript JSON paths, excluding analysis/KG files."""
    return sorted(
        p for p in text_dir.glob("*.json")
        if not any(pat in p.name for pat in _EXCLUDE_PATTERNS)
    )


def available_videos(video_dir: Path = VIDEO_DIR) -> list[Path]:
    if not video_dir.is_dir():
        return []
    return sorted(p for p in video_dir.iterdir()
                  if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".avi"})


# Transcript cache: avoid re-reading JSON on every search
@lru_cache(maxsize=64)
def _cached_load(path_str: str) -> Transcript:
    return Transcript.load(Path(path_str))


def search_all(
    query: str,
    *,
    top_k: int = 10,
    min_score: float = 0.0,
    text_dir: Path = TEXT_DIR,
) -> list[SearchResult]:
    """Search across ALL transcripts in the library, return globally ranked results."""
    all_results: list[SearchResult] = []
    for tpath in available_transcripts(text_dir):
        try:
            tr = _cached_load(str(tpath))
            all_results.extend(tr.scored_search(query, top_k=top_k, min_score=min_score))
        except Exception:
            continue
    all_results.sort(key=lambda r: (-r.score, r.start))
    return all_results[:top_k]


if __name__ == "__main__":
    import argparse
    import sys

    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Text to video timestamp (CLI)")
    parser.add_argument("text", help="Text to locate; use -r for KB reference format")
    parser.add_argument("--transcript", help="Transcript JSON path (default: first in static/txt)")
    parser.add_argument("--reference", "-r", action="store_true", help="Parse text as KB reference")
    parser.add_argument("--all", action="store_true", help="Search across all transcripts")
    args = parser.parse_args()

    if args.all:
        results = search_all(args.text, top_k=10, min_score=0.0)
        print(f"Found {len(results)} results across all transcripts:")
        for i, r in enumerate(results, 1):
            print(f"  [{i}] {r.video_name} @ {r.start:.2f}s (score={r.score:.2f}) {r.text[:80]}")
    elif args.reference:
        stem, needle = parse_kb_reference(args.text)
        print(f"Parsed reference -> doc: {stem!r}, text: {(needle or '')[:60]!r}")
        if not stem or not needle:
            raise SystemExit("Cannot parse reference (expected '] xxx.txt:\\n\\ntext\\n\\n...' format)")
        video, transcript_path = resolve(stem)
        print(f"Video: {video}")
        print(f"Transcript: {transcript_path}")
        if transcript_path:
            tr = Transcript.load(transcript_path)
            result = tr.locate(needle)
            print(f"Result: {result}")
    else:
        needle = args.text
        transcript_path = Path(args.transcript) if args.transcript else (available_transcripts() or [None])[0]
        if not transcript_path:
            raise SystemExit("No transcript JSON found in static/txt")
        tr = Transcript.load(transcript_path)
        print(f"Transcript: {tr.path.name} ({len(tr.segments)} segments, {tr.duration:.2f}s, lang={tr.language})")
        if not tr.text_matches_segments:
            print("Warning: json text != joined segments; using joined segments")
        result = tr.locate(needle)
        print(f"Result: {result}")
