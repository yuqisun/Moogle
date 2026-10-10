"""Static file server for the Moogle video library (Starlette + uvicorn).

Serves the ``static/`` directory — the HTML5 player page, videos, subtitles and
the transcript-derived JSON — with full HTTP Range support so that video seeking
works on first load instead of requiring a full download.

Range handling, ``ETag``, ``If-Range``, ``If-None-Match`` and
``multipart/byteranges`` responses are all provided by Starlette's
``FileResponse``; uvicorn is the ASGI server.  Both already ship as Streamlit
dependencies, so this adds no new packages.

Usage (same CLI as the previous stdlib implementation):
    python static_server.py [port] [--bind ADDR] [--directory DIR]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

# Default document root: <repo>/static.  The previous implementation defaulted
# to os.getcwd(), which silently served the project root and 404'd every player
# asset ("/player/player.html" resolved to <repo>/player/player.html).
REPO = Path(__file__).resolve().parent
DEFAULT_DIRECTORY = REPO / "static"

# Canonical player route, so a shared link reads
#   http://host:8502/moogle?s=...&t=...
# instead of exposing the file path.  streamlit_app._player_url() must stay in
# sync with this.  The legacy /player/player.html keeps working — the
# StaticFiles mount below still serves it.
PLAYER_ROUTE = "/moogle"


# ---------------------------------------------------------------- .env config

def _load_env(env_path: Path = REPO / ".env") -> dict[str, str]:
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


_ENV = _load_env()
_LLM_API_KEY = _ENV.get("LLM_API_KEY", "")
_LLM_API_BASE = _ENV.get("LLM_API_BASE", "https://api.deepseek.com/v1")
_LLM_MODEL = _ENV.get("LLM_MODEL", "deepseek-chat")


class AccessLogMiddleware:
    """Lightweight ASGI access logger — no body buffering, safe for large files.

    Prints one line per completed request::

        HH:MM:SS  192.168.1.5     GET  206      0.5ms  /video/x.mp4  bytes=0-524287  524288B
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = client[0] if client else "-"
        method = scope.get("method", "")
        path = scope.get("path", "")
        qs = scope.get("query_string", b"").decode("latin-1")
        url = path + (f"?{qs}" if qs else "")

        # Extract Range header from the request (useful for video playback)
        range_hdr = ""
        for hdr_name, hdr_value in scope.get("headers", []):
            if hdr_name == b"range":
                range_hdr = hdr_value.decode("latin-1")
                break

        status_code = 0
        content_length = ""
        start = time.monotonic()

        async def send_wrapper(message):
            nonlocal status_code, content_length
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
                for hdr_name, hdr_value in message.get("headers", []):
                    if hdr_name == b"content-length":
                        content_length = hdr_value.decode("latin-1")
                        break
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            ts = datetime.now().strftime("%H:%M:%S")
            extras = []
            if range_hdr:
                extras.append(range_hdr)
            if content_length:
                extras.append(f"{content_length}B")
            extra_str = ("  " + "  ".join(extras)) if extras else ""
            print(
                f"{ts}  {client_ip:<15} {method:<4} {status_code}  "
                f"{elapsed_ms:>7.1f}ms  {url}{extra_str}",
                flush=True,
            )


def build_app(directory: Path) -> Starlette:
    """Return an ASGI app that serves *directory* at the URL root."""
    player_page = directory / "player" / "player.html"
    if not player_page.is_file():
        # Fail loudly at startup rather than 500 on the first visit.
        raise RuntimeError(f"Player page not found: {player_page}")

    txt_dir = directory / "txt"

    async def serve_player(request) -> FileResponse:
        return FileResponse(player_page)

    # ------------------------------------------------------------------ /api/chat

    async def chat_api(request: Request):
        """POST /api/chat — answer a question about a video's subtitles via SSE.

        Body: ``{"question": "...", "stem": "..."}``
        Streams ``text/event-stream`` with ``data: {"content": "..."}`` chunks.
        """
        # --- Parse & validate body -------------------------------------------
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        question = body.get("question") if isinstance(body, dict) else None
        stem = body.get("stem") if isinstance(body, dict) else None
        if not question or not stem:
            return JSONResponse(
                {"error": "Both 'question' and 'stem' fields are required"},
                status_code=400,
            )

        question = question.strip()
        stem = stem.strip()
        if len(question) > 2000:
            return JSONResponse({"error": "Question too long (max 2000 chars)"}, status_code=400)
        if len(stem) > 500:
            return JSONResponse({"error": "Stem too long (max 500 chars)"}, status_code=400)

        # --- Load subtitle JSON ------------------------------------------------
        sub_path = (txt_dir / f"{stem}.json").resolve()
        if not str(sub_path).startswith(str(txt_dir.resolve())):
            return JSONResponse({"error": "Invalid stem"}, status_code=400)
        if not sub_path.is_file():
            return JSONResponse(
                {"error": f"Subtitle file not found: {stem}.json"},
                status_code=404,
            )

        try:
            sub_data = json.loads(sub_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"ERROR reading subtitle file {sub_path}: {exc}", flush=True)
            return JSONResponse(
                {"error": "Failed to read subtitle file"},
                status_code=500,
            )

        segments = sub_data.get("segments", [])

        # --- Build prompt with segment timestamps -----------------------------
        transcript_lines = []
        for seg in segments:
            start = seg.get("start", 0)
            text = seg.get("text", "").strip()
            transcript_lines.append(f"[ts:{start}] {text}")
        transcript_block = "\n".join(transcript_lines)

        prompt = (
            f"User question: {question}\n\n"
            "The following is the complete subtitle transcript of the current video, "
            "with timestamps for each segment:\n\n"
            f"{transcript_block}\n\n"
            "Please answer the user's question based on this transcript. "
            "When referencing specific content, insert timestamp markers in the exact "
            "format [ts:SECONDS] where SECONDS is the numeric start time from the "
            "segments above. You may include multiple [ts:...] markers when citing "
            "different parts. Reply in the same language as the question. Be concise."
        )

        # --- No API key fallback ----------------------------------------------
        if not _LLM_API_KEY:
            async def fallback_gen():
                msg = "LLM API key is not configured. Please set LLM_API_KEY in .env to enable AI chat."
                yield f"data: {json.dumps({'content': msg})}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(
                fallback_gen(),
                media_type="text/event-stream",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        # --- SSE streaming generator (sync — Starlette runs it in threadpool) -
        def sse_generator():
            payload = json.dumps({
                "model": _LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 1024,
                "stream": True,
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{_LLM_API_BASE.rstrip('/')}/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {_LLM_API_KEY}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    for raw_line in resp:
                        line = raw_line.decode("utf-8").strip()
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield f"data: {json.dumps({'content': content})}\n\n"
                            except json.JSONDecodeError:
                                continue
            except Exception as exc:
                err_msg = f"LLM call failed: {exc}"
                yield f"data: {json.dumps({'content': err_msg})}\n\n"

            yield "data: [DONE]\n\n"

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # ---------------------------------------------------------------- routes

    return AccessLogMiddleware(Starlette(
        routes=[
            Route(PLAYER_ROUTE, serve_player),
            # Also accept a stray trailing slash.  The Mount below would
            # otherwise catch "/moogle/" and look for a directory of that name.
            Route(f"{PLAYER_ROUTE}/", serve_player),
            Route("/api/chat", chat_api, methods=["POST"]),
            Mount("/", app=StaticFiles(directory=str(directory), check_dir=True)),
        ]
    ))


def main() -> None:
    parser = argparse.ArgumentParser(description="Static file server with HTTP Range support")
    parser.add_argument("port", type=int, nargs="?", default=8000, help="Port to bind (default: 8000)")
    parser.add_argument("--bind", "-b", default="", metavar="ADDRESS", help="Specify alternate bind address")
    parser.add_argument(
        "--directory",
        "-d",
        default=str(DEFAULT_DIRECTORY),
        metavar="DIR",
        help="Directory to serve files from (default: <repo>/static)",
    )
    args = parser.parse_args()

    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        raise SystemExit(f"ERROR: --directory is not a directory: {directory}")

    host = args.bind or "0.0.0.0"
    print(f"Serving {directory} on http://{host}:{args.port} (Range requests enabled)", flush=True)
    uvicorn.run(build_app(directory), host=host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
