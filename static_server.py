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
import time
from datetime import datetime
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.responses import FileResponse
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

    async def serve_player(request) -> FileResponse:
        return FileResponse(player_page)

    return AccessLogMiddleware(Starlette(
        routes=[
            Route(PLAYER_ROUTE, serve_player),
            # Also accept a stray trailing slash.  The Mount below would
            # otherwise catch "/moogle/" and look for a directory of that name.
            Route(f"{PLAYER_ROUTE}/", serve_player),
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
