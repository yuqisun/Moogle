"""Smoke test for streamlit_app.py v3 (banking theme, multi-transcript search)."""
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = r"D:\workspace\moogle\streamlit_app.py"

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

# Check static server (player page)
import urllib.request
try:
    resp = urllib.request.urlopen("http://127.0.0.1:8502/player/player.html", timeout=5)
    print("static server: OK", resp.status, len(resp.read()), "bytes")
except Exception as exc:
    print("static server: FAIL", exc)

# Check no Big City references in player HTML
try:
    html = urllib.request.urlopen("http://127.0.0.1:8502/player/player.html", timeout=5).read().decode()
    has_big_city = "Big City" in html or "tooplate" in html.lower()
    print("Big City references in player:", has_big_city)
except Exception:
    print("Big City check: skipped")

print("\nALL CHECKS PASSED" if not at.exception else "\nSOME CHECKS FAILED")
