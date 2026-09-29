"""Moogle - Search any moment across all videos (Streamlit frontend v3).

Banking/financial institution theme. Searches across ALL transcripts in the library.
Uses DeepSeek LLM for generating responses.

Launch:
    .venv\\Scripts\\streamlit.exe run streamlit_app.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import streamlit as st

import video_locator as vl

# ---------------------------------------------------------------- Config
REPO = Path(__file__).resolve().parent


def load_env(env_path: Path = REPO / ".env") -> dict[str, str]:
    """Minimal .env parser (KEY=VALUE, skip comments and blanks)."""
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
SEARCH_TOP_K = int(CFG.get("SEARCH_TOP_K", "10"))
SEARCH_MIN_SCORE = float(CFG.get("SEARCH_MIN_SCORE", "0.3"))
LLM_API_KEY = CFG.get("LLM_API_KEY", "")
LLM_API_BASE = CFG.get("LLM_API_BASE", "https://api.deepseek.com/v1")
LLM_MODEL = CFG.get("LLM_MODEL", "deepseek-chat")
STATIC_PORT = int(CFG.get("STATIC_PORT", "8502"))

PLAYER_BASE_URL = f"http://127.0.0.1:{STATIC_PORT}/player/player.html"

# ---------------------------------------------------------------- Page config
st.set_page_config(page_title="Moogle - Video Content Locator", layout="wide")

# ---------------------------------------------------------------- LLM call

def call_llm(question: str, results: list[vl.SearchResult]) -> str:
    """Generate an LLM response from search results. Falls back to template if no API key."""
    context_parts = []
    for i, r in enumerate(results, 1):
        src = f" [{r.video_name}]" if r.video_name else ""
        context_parts.append(f"[{i}] ({r.start:.1f}s){src} {r.text}")
    context = "\n".join(context_parts)

    if not LLM_API_KEY:
        lines = [f"Found {len(results)} relevant segment(s) in the transcript library:\n"]
        for i, r in enumerate(results, 1):
            mins = int(r.start // 60)
            secs = int(r.start % 60)
            src = f" ({r.video_name})" if r.video_name else ""
            lines.append(f"**[{i}]** {mins:02d}:{secs:02d}{src} — {r.text[:120]}{'...' if len(r.text) > 120 else ''}")
        lines.append("\n> Configure `LLM_API_KEY` in `.env` to enable AI-powered summaries.")
        return "\n\n".join(lines)

    prompt = (
        f"User question: {question}\n\n"
        f"The following are relevant segments retrieved from video transcripts:\n{context}\n\n"
        "Please answer the user's question based on these segments. "
        "If the segments are insufficient, say so. "
        "Reply in the same language as the question. Be concise and professional."
    )
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 1024,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{LLM_API_BASE.rstrip('/')}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception as exc:
        return f"LLM call failed: {exc}\n\nRaw search results:\n\n{context}"


# ---------------------------------------------------------------- Data loading

@st.cache_data(show_spinner="Loading transcripts...")
def load_transcript(path_str: str) -> vl.Transcript:
    return vl.Transcript.load(Path(path_str))


@st.cache_data(show_spinner="Scanning video library...")
def get_library_info():
    """Return (videos, transcripts, video-to-transcript mapping)."""
    videos = vl.available_videos()
    transcripts = vl.available_transcripts()
    # Map each video stem to its transcript path
    vid_to_tr: dict[str, str] = {}
    for t in transcripts:
        tr = vl.Transcript.load(t)
        vname = tr.video_name
        vid_to_tr[vname] = str(t)
    return videos, transcripts, vid_to_tr


videos, transcripts, vid_to_tr = get_library_info()

if not transcripts:
    st.error(f"No transcript JSON files found in `{vl.TEXT_DIR}`")
    st.stop()

# ---------------------------------------------------------------- Session state
if "messages" not in st.session_state:
    st.session_state.messages = []

# ---------------------------------------------------------------- Sidebar
with st.sidebar:
    st.title("Moogle")
    st.caption("Search any moment across all videos")
    st.divider()

    # Video list with links to player page (scrollable, filterable)
    st.markdown(f"**Video Library** ({len(videos)})")

    # Search / filter box
    video_filter = st.text_input("Filter videos", placeholder="Type to filter...", key="video_filter", label_visibility="collapsed")

    # Build the scrollable list
    filtered_videos = videos
    if video_filter:
        kw = video_filter.lower()
        filtered_videos = [v for v in videos if kw in v.stem.lower()]

    list_html = '<div style="max-height:400px; overflow-y:auto; padding-right:8px;">'
    for v in filtered_videos:
        tr_path = vid_to_tr.get(v.stem)
        if tr_path:
            tr_obj = load_transcript(tr_path)
            mins = int(tr_obj.duration // 60)
            secs = int(tr_obj.duration % 60)
            dur = f"{mins}:{secs:02d}"
            segs = len(tr_obj.segments)
        else:
            dur = "?"
            segs = 0

        video_abs = f"/video/{urllib.parse.quote(v.name)}"
        link = (f"{PLAYER_BASE_URL}?src={video_abs}&t=0"
                f"&title={urllib.parse.quote(v.stem)}")
        list_html += (
            f'<div style="padding:6px 0; border-bottom:1px solid rgba(128,128,128,0.2);">'
            f'<a href="{link}" target="_blank" style="text-decoration:none; color:inherit;">'
            f'<div style="font-size:0.9em; font-weight:500;">{v.stem}</div>'
            f'<div style="font-size:0.75em; opacity:0.6;">{dur} · {segs} segments</div>'
            f'</a></div>'
        )

    if not filtered_videos:
        list_html += '<div style="padding:12px 0; opacity:0.5; font-size:0.9em;">No matching videos.</div>'

    list_html += '</div>'
    if filtered_videos and video_filter:
        list_html = f'<div style="font-size:0.75em; opacity:0.5; margin-bottom:4px;">{len(filtered_videos)} of {len(videos)} videos</div>' + list_html

    st.markdown(list_html, unsafe_allow_html=True)

    st.divider()

    # Dynamic search config
    st.session_state["search_min_score"] = st.slider("Min relevance score", 0.0, 1.0, float(CFG.get("SEARCH_MIN_SCORE", "0.3")), 0.05)
    st.session_state["search_top_k"] = st.slider("Max results", 1, 20, int(CFG.get("SEARCH_TOP_K", "10")))

    if LLM_API_KEY:
        st.caption(f"LLM: {LLM_MODEL} @ {LLM_API_BASE.split('//')[1].split('/')[0]}")
    else:
        st.caption("LLM: not configured (template replies)")

# ---------------------------------------------------------------- Main: Chat area
st.title("Welcome to Moogle")
st.caption(
    "Ask a question or enter keywords — I will search across all video transcripts "
    "in the library, generate a response, and provide timestamped reference links "
    "that jump directly to the relevant moment in the video."
)

# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"], unsafe_allow_html=True)

# User input
if question := st.chat_input("Ask a question about the video content..."):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # Search across ALL transcripts
    with st.spinner("Searching transcript library..."):
        results = vl.search_all(
            question,
            top_k=st.session_state["search_top_k"],
            min_score=st.session_state["search_min_score"],
        )

    if not results:
        reply = "No relevant content found in the transcript library. Try different keywords."
        st.session_state.messages.append({"role": "assistant", "content": reply})
        with st.chat_message("assistant"):
            st.markdown(reply)
    else:
        # LLM response
        with st.spinner("Generating response..."):
            llm_reply = call_llm(question, results)

        # Build citation links
        cite_lines = []
        for i, r in enumerate(results, 1):
            mins = int(r.start // 60)
            secs = int(r.start % 60)
            time_str = f"{mins:02d}:{secs:02d}"
            video_abs = f"/video/{urllib.parse.quote(r.video_name + '.mp4')}"
            cited = urllib.parse.quote(r.text[:200])
            link = (
                f"{PLAYER_BASE_URL}?src={video_abs}"
                f"&t={r.start:.2f}"
                f"&title={urllib.parse.quote(r.video_name)}"
                f"&cite={cited}"
            )
            score_pct = int(r.score * 100)
            src_label = f" ({r.video_name})" if r.video_name else ""
            cite_lines.append(
                f"**[{i}]** [{time_str} ({r.start:.1f}s)]({link}){src_label} "
                f"— {r.text[:100]}{'...' if len(r.text) > 100 else ''} "
                f"`relevance {score_pct}%`"
            )

        full_reply = llm_reply + "\n\n---\n\n**Reference Segments:**\n\n" + "\n\n".join(cite_lines)
        st.session_state.messages.append({"role": "assistant", "content": full_reply})

        with st.chat_message("assistant"):
            st.markdown(full_reply, unsafe_allow_html=True)
