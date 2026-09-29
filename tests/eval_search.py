"""Evaluate search quality: Precision@K, Recall@K, MRR, timestamp accuracy.

Usage:
    python tests/eval_search.py              # evaluate current algorithm
    python tests/eval_search.py --top-k 5    # evaluate at K=5 (default 10)

Reads tests/search_queries.json for ground truth, runs the search, computes metrics.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import video_locator as vl


def load_test_queries(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate(top_k: int = 10) -> None:
    queries_path = REPO / "tests" / "search_queries.json"
    test_queries = load_test_queries(queries_path)

    # Load the transcript once
    transcript_path = REPO / "static" / "txt" / "2.02 - 1.1什么是神经网络(Av73508149,P2)_en.json"
    if not transcript_path.exists():
        print(f"ERROR: transcript not found: {transcript_path}")
        sys.exit(1)

    tr = vl.Transcript.load(transcript_path)
    print(f"Transcript: {tr.path.name} ({len(tr.segments)} segments, {tr.duration:.1f}s)")
    print(f"Evaluating {len(test_queries)} queries at top-{top_k}\n")

    all_precision = []
    all_recall = []
    all_rr = []  # reciprocal rank
    all_ts_error = []

    header = f"{'Query':<50} | {'P@K':>5} | {'R@K':>5} | {'MRR':>5} | {'AvgΔt':>6}"
    print(header)
    print("-" * len(header))

    for tq in test_queries:
        query = tq["query"]
        relevant = set(tq["relevant_segments"])
        n_relevant = len(relevant)

        # Run search on this single transcript
        results = tr.scored_search(query, top_k=top_k, min_score=0.0)

        # Compute metrics
        retrieved_indices = [r.segment_index for r in results[:top_k]]
        retrieved_set = set(retrieved_indices)

        # Precision@K
        hits = len(retrieved_set & relevant)
        precision = hits / min(top_k, len(results)) if results else 0.0

        # Recall@K
        recall = hits / n_relevant if n_relevant > 0 else 0.0

        # Reciprocal Rank (position of first relevant result)
        rr = 0.0
        for rank, idx in enumerate(retrieved_indices, 1):
            if idx in relevant:
                rr = 1.0 / rank
                break

        # Timestamp accuracy: for each relevant segment that was retrieved,
        # check how close the returned timestamp is to the expected one
        ts_errors = []
        for r in results[:top_k]:
            if r.segment_index in relevant:
                expected_start = tr.segments[r.segment_index].start
                ts_errors.append(abs(r.start - expected_start))

        avg_ts_error = sum(ts_errors) / len(ts_errors) if ts_errors else float("nan")

        all_precision.append(precision)
        all_recall.append(recall)
        all_rr.append(rr)
        if ts_errors:
            all_ts_error.extend(ts_errors)

        # Display (truncate query for display)
        q_display = query[:48] + ".." if len(query) > 48 else query
        ts_str = f"{avg_ts_error:5.1f}s" if ts_errors else "   N/A"
        print(f"{q_display:<50} | {precision:5.2f} | {recall:5.2f} | {rr:5.2f} | {ts_str}")

    # Averages
    avg_p = sum(all_precision) / len(all_precision) if all_precision else 0
    avg_r = sum(all_recall) / len(all_recall) if all_recall else 0
    avg_mrr = sum(all_rr) / len(all_rr) if all_rr else 0
    avg_ts = sum(all_ts_error) / len(all_ts_error) if all_ts_error else float("nan")

    print("-" * len(header))
    ts_avg_str = f"{avg_ts:5.1f}s" if all_ts_error else "   N/A"
    print(f"{'AVERAGE':<50} | {avg_p:5.2f} | {avg_r:5.2f} | {avg_mrr:5.2f} | {ts_avg_str}")
    print()

    # Also output machine-readable summary
    summary = {
        "top_k": top_k,
        "num_queries": len(test_queries),
        "avg_precision": round(avg_p, 4),
        "avg_recall": round(avg_r, 4),
        "avg_mrr": round(avg_mrr, 4),
        "avg_timestamp_error_s": round(avg_ts, 2) if all_ts_error else None,
    }
    print(f"JSON: {json.dumps(summary)}")


if __name__ == "__main__":
    import argparse

    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Evaluate search quality")
    parser.add_argument("--top-k", type=int, default=10, help="Evaluate at top-K (default 10)")
    args = parser.parse_args()

    evaluate(top_k=args.top_k)
