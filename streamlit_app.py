"""Moogle - Search any moment across all videos (Streamlit frontend v3).

Banking/financial institution theme. Searches across ALL transcripts in the library.
Uses DeepSeek LLM for generating responses.

Launch:
    .venv\\Scripts\\streamlit.exe run streamlit_app.py
"""
from __future__ import annotations

import json
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

# Use a relative-style base so links work from any host (localhost or remote).
# Streamlit serves on its own port; the static server is on STATIC_PORT.
# We build the full URL at render time using the browser's current host.
_PLAYER_PATH = f":{STATIC_PORT}/player/player.html"


def _player_url(path_suffix: str = "") -> str:
    """Build player URL using the current request's hostname.

    Falls back to 127.0.0.1 when no active session (e.g. during caching).
    This ensures links work correctly when accessed from remote machines.
    """
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        ctx = get_script_run_ctx()
        if ctx and ctx.session_client:
            host_header = ctx.session_client.request.headers.get("Host", f"127.0.0.1:{STATIC_PORT}")
            hostname = host_header.split(":")[0]
            return f"http://{hostname}:{STATIC_PORT}/player/player.html{path_suffix}"
    except Exception:
        pass
    return f"http://127.0.0.1:{STATIC_PORT}/player/player.html{path_suffix}"

# ---------------------------------------------------------------- Page config
st.set_page_config(page_title="Moogle - Video Content Locator", layout="wide")

# ---------------------------------------------------------------- LLM call

def _build_context(results: list[vl.SearchResult]) -> str:
    """Build context string from search results."""
    context_parts = []
    for i, r in enumerate(results, 1):
        src = f" [{r.video_name}]" if r.video_name else ""
        context_parts.append(f"[{i}] ({r.start:.1f}s){src} {r.text}")
    return "\n".join(context_parts)


def _build_prompt(question: str, context: str) -> str:
    """Build the LLM prompt."""
    return (
        f"User question: {question}\n\n"
        f"The following are relevant segments retrieved from video transcripts:\n{context}\n\n"
        "Please answer the user's question based on these segments. "
        "If the segments are insufficient, say so. "
        "Reply in the same language as the question. Be concise and professional."
    )


def call_llm_stream(question: str, results: list[vl.SearchResult]):
    """Stream LLM response token by token. Yields str chunks.

    Falls back to a single yield of template text if no API key is configured.
    """
    context = _build_context(results)

    if not LLM_API_KEY:
        lines = [f"Found {len(results)} relevant segment(s) in the transcript library:\n"]
        for i, r in enumerate(results, 1):
            mins = int(r.start // 60)
            secs = int(r.start % 60)
            src = f" ({r.video_name})" if r.video_name else ""
            lines.append(f"**[{i}]** {mins:02d}:{secs:02d}{src} — {r.text[:120]}{'...' if len(r.text) > 120 else ''}")
        lines.append("\n> Configure `LLM_API_KEY` in `.env` to enable AI-powered summaries.")
        yield "\n\n".join(lines)
        return

    prompt = _build_prompt(question, context)
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 1024,
        "stream": True,
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
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                # SSE lines are prefixed with "data: "
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue
    except Exception as exc:
        yield f"LLM call failed: {exc}\n\nRaw search results:\n\n{context}"


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

    if st.button("➕ New Chat", use_container_width=True, type="primary"):
        st.session_state.messages = []
        st.rerun()

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
        link = (f"{_player_url()}?src={video_abs}&t=0"
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
        # Build citation links (prepared ahead, appended after streaming)
        cite_lines = []
        for i, r in enumerate(results, 1):
            mins = int(r.start // 60)
            secs = int(r.start % 60)
            time_str = f"{mins:02d}:{secs:02d}"
            video_abs = f"/video/{urllib.parse.quote(r.video_name + '.mp4')}"
            cited = urllib.parse.quote(r.text[:200])
            link = (
                f"{_player_url()}?src={video_abs}"
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
        citations_md = "\n\n---\n\n**Reference Segments:**\n\n" + "\n\n".join(cite_lines)

        # Stream LLM response token by token
        with st.chat_message("assistant"):
            placeholder = st.empty()
            llm_reply = ""
            last_render_time = 0.0
            render_interval = 0.05  # throttle: max 20 renders/sec
            try:
                for token in call_llm_stream(question, results):
                    llm_reply += token
                    now = time.monotonic()
                    if now - last_render_time >= render_interval:
                        placeholder.markdown(llm_reply, unsafe_allow_html=True)
                        last_render_time = now
            except Exception as exc:
                llm_reply += f"\n\n*Stream interrupted: {exc}*"

            # Final render: flush any remaining tokens + append citations
            full_reply = llm_reply + citations_md
            placeholder.markdown(full_reply, unsafe_allow_html=True)

        st.session_state.messages.append({"role": "assistant", "content": full_reply})
