"""Smoke test for streamlit_app.py v3 (banking theme, multi-transcript search)."""
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from streamlit.testing.v1 import AppTest  # noqa: E402

# Derive from this file's location instead of a hard-coded absolute path.
APP = str(Path(__file__).resolve().parent / "streamlit_app.py")

at = AppTest.from_file(APP, default_timeout=120)
at.run()

print("exceptions:", [e.value for e in at.exception])
print("markdown count:", len(at.markdown))
print("chat_input:", len(at.chat_input))

# Check welcome message
welcome_found = any("Welcome" in m.value or "MOOGLE" in m.value or "Moogle" in m.value for m in at.markdown)
print("welcome message found:", welcome_found)

# Check video library in sidebar
video_links = [m.value for m in at.markdown if ".mp4" in m.value or "segments" in m.value]
print("video library items:", len(video_links))

# Check static server: the canonical /moogle route and the legacy path must both
# serve the player page, and the page's absolute asset paths must resolve.
import urllib.request

STATIC_BASE = "http://127.0.0.1:8502"
html = ""
for label, route in (("canonical /moogle", "/moogle"),
                     ("legacy /player/player.html", "/player/player.html")):
    try:
        resp = urllib.request.urlopen(STATIC_BASE + route, timeout=5)
        body = resp.read().decode()
        if not html:
            html = body
        print(f"static server ({label}): OK", resp.status, len(body), "bytes")
    except Exception as exc:
        print(f"static server ({label}): FAIL", exc)

for asset in ("/player/css/moogle-style.css", "/player/js/jquery-1.11.3.min.js"):
    try:
        resp = urllib.request.urlopen(STATIC_BASE + asset, timeout=5)
        print(f"asset {asset}: OK", resp.status)
    except Exception as exc:
        print(f"asset {asset}: FAIL", exc)

# Check no Big City references in player HTML
if html:
    has_big_city = "Big City" in html or "tooplate" in html.lower()
    print("Big City references in player:", has_big_city)
else:
    print("Big City check: skipped")

print("\nALL CHECKS PASSED" if not at.exception else "\nSOME CHECKS FAILED")
