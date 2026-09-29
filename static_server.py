"""Static file server with HTTP Range request support.

Python's built-in http.server does NOT support Range requests, which breaks
HTML5 video seeking (the browser cannot jump to a timestamp without Range
support — it must download the entire file sequentially first).

This server is a drop-in replacement that adds Range support so that:
  - Video seek / Media Fragment URIs (#t=91.10) work on first load
  - Large videos stream efficiently instead of requiring full download

Usage (same CLI as http.server):
    python static_server.py [port] [--bind ADDR] [--directory DIR]
"""
from __future__ import annotations

import argparse
import os
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class RangeHTTPRequestHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler with HTTP Range (partial content) support."""

    def send_head(self):
        """Override to intercept GET/HEAD and serve partial content when requested."""
        if self.command not in ("GET", "HEAD"):
            return super().send_head()

        path = self.translate_path(self.path)
        if os.path.isdir(path):
            # Let the parent handle directory listings normally
            return super().send_head()

        range_header = self.headers.get("Range")
        if not range_header:
            # No Range header — fall back to normal full-file response.
            # Accept-Ranges is added by our end_headers() override.
            return super().send_head()

        # Parse Range header: bytes=start-end
        m = re.match(r"bytes=(\d*)-(\d*)", range_header)
        if not m:
            return super().send_head()

        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        try:
            file_size = os.fstat(f.fileno()).st_size
            start_str, end_str = m.group(1), m.group(2)

            if start_str == "" and end_str == "":
                f.close()
                return super().send_head()

            if start_str == "":
                # bytes=-N means last N bytes
                length = int(end_str)
                start = max(0, file_size - length)
                end = file_size - 1
            elif end_str == "":
                # bytes=N- means from N to end
                start = int(start_str)
                end = file_size - 1
            else:
                start = int(start_str)
                end = int(end_str)

            if start > end or start >= file_size:
                f.close()
                # Manually send 416 — cannot use send_error() because it
                # calls end_headers() internally, preventing us from adding
                # the Content-Range header afterwards.
                self.send_response(416, "Requested Range Not Satisfiable")
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.send_header("Content-Length", "0")
                SimpleHTTPRequestHandler.end_headers(self)
                return None

            # Clamp end to file size
            end = min(end, file_size - 1)
            content_length = end - start + 1

            # Determine content type
            ctype = self.guess_type(path)

            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(content_length))
            self.send_header("Last-Modified", self.date_time_string(os.fstat(f.fileno()).st_mtime))
            # Don't call self.end_headers() here — it would add a duplicate
            # Accept-Ranges. Call the parent's end_headers directly.
            SimpleHTTPRequestHandler.end_headers(self)

            f.seek(start)
            # Return a wrapper that limits reads to content_length
            return _RangeFileWrapper(f, content_length)

        except Exception:
            f.close()
            raise

    def end_headers(self):
        """Add Accept-Ranges to all non-206 responses so browsers know we support partial content."""
        # Only add Accept-Ranges for full responses (not 206 partial — those
        # already include it explicitly, and adding it again via this override
        # would duplicate it).
        if getattr(self, '_response_code', 200) != 206:
            self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_response(self, code, message=None):
        """Track response code so end_headers knows whether to add Accept-Ranges."""
        self._response_code = code
        super().send_response(code, message)


class _RangeFileWrapper:
    """Wraps a file object to limit reading to a specific byte count."""

    def __init__(self, f, remaining):
        self._f = f
        self._remaining = remaining

    def read(self, n=-1):
        if self._remaining <= 0:
            return b""
        if n < 0:
            n = self._remaining
        n = min(n, self._remaining)
        data = self._f.read(n)
        self._remaining -= len(data)
        return data

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def main():
    parser = argparse.ArgumentParser(description="Static file server with Range request support")
    parser.add_argument("port", type=int, nargs="?", default=8000, help="Port to bind (default: 8000)")
    parser.add_argument("--bind", "-b", default="", metavar="ADDRESS", help="Specify alternate bind address")
    parser.add_argument("--directory", "-d", default=os.getcwd(), help="Directory to serve files from")
    args = parser.parse_args()

    handler = partial(RangeHTTPRequestHandler, directory=args.directory)
    server = ThreadingHTTPServer((args.bind, args.port), handler)

    addr = server.server_address
    print(f"Serving {os.path.abspath(args.directory)} on http://{addr[0] or '0.0.0.0'}:{addr[1]} (Range requests enabled)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
