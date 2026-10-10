# Video Player Chatbot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a floating chatbot widget to the video player page that answers questions about the current video's subtitles using the DeepSeek LLM, with inline clickable timestamp citations.

**Architecture:** A new `chatbot.js` frontend widget (floating bubble + panel) communicates with a new `/api/chat` endpoint added to `static_server.py`. The server loads the subtitle JSON, builds a prompt with full transcript context, streams the LLM response via SSE, and the frontend renders tokens incrementally while parsing `[ts:SECONDS]` markers into clickable gold capsule links.

**Tech Stack:** Vanilla JS (ES5-compatible, matching existing codebase), Python stdlib `http.server`, DeepSeek OpenAI-compatible API, SSE streaming.

---

### Task 1: Backend — Add `/api/chat` endpoint to static_server.py

**Files:**
- Modify: `static_server.py`
- Test: manual curl test

- [ ] **Step 1: Add imports and config loading at top of file**

Add after line 19 (`from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer`):

```python
import json
import urllib.request
from pathlib import Path


def _load_env(env_path: str = ".env") -> dict:
    """Minimal .env parser (KEY=VALUE, skip comments and blanks)."""
    config: dict[str, str] = {}
    p = Path(env_path)
    if not p.exists():
        return config
    for line in p.read_text(encoding="utf-8").splitlines():
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
```

- [ ] **Step 2: Add `_handle_chat_api` method to `RangeHTTPRequestHandler`**

Add this method inside the `RangeHTTPRequestHandler` class, after the `send_response` method (after line 121):

```python
    def _handle_chat_api(self):
        """Handle POST /api/chat — stream LLM response over SSE."""
        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_error(400, "Empty request body")
            return
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        question = data.get("question", "").strip()
        stem = data.get("stem", "").strip()
        if not question or not stem:
            self.send_error(400, "Missing 'question' or 'stem' field")
            return

        # Load subtitle JSON
        txt_path = os.path.join(self.directory, "txt", stem + ".json")
        if not os.path.isfile(txt_path):
            self.send_error(404, f"Subtitle file not found: {stem}")
            return

        with open(txt_path, "r", encoding="utf-8") as f:
            subtitle_data = json.load(f)

        full_text = subtitle_data.get("text", "")
        segments = subtitle_data.get("segments", [])

        # Build segment index for the prompt
        seg_index = "\n".join(
            f"[{s['start']:.1f}s] {s['text'].strip()}" for s in segments
        )

        prompt = (
            f"User question: {question}\n\n"
            f"The following is the complete subtitle transcript of the current video, "
            f"with timestamps for each segment:\n\n{seg_index}\n\n"
            f"Please answer the user's question based on this transcript. "
            f"When referencing specific content, insert timestamp markers in the exact format "
            f"[ts:SECONDS] where SECONDS is the numeric start time from the segments above. "
            f"You may include multiple [ts:...] markers when citing different parts. "
            f"Reply in the same language as the question. Be concise."
        )

        # Send SSE headers
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        SimpleHTTPRequestHandler.end_headers(self)

        # Fallback if no API key
        if not _LLM_API_KEY:
            fallback = "LLM API key not configured. Please set LLM_API_KEY in .env file."
            self.wfile.write(f"data: {json.dumps({'content': fallback})}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        # Call LLM API (streaming)
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
                                sse_data = json.dumps({"content": content})
                                self.wfile.write(f"data: {sse_data}\n\n".encode("utf-8"))
                                self.wfile.flush()
                        except json.JSONDecodeError:
                            continue
        except Exception as exc:
            err_msg = json.dumps({"content": f"LLM call failed: {exc}"})
            self.wfile.write(f"data: {err_msg}\n\n".encode("utf-8"))
            self.wfile.flush()

        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
```

- [ ] **Step 3: Override `do_POST` and `do_OPTIONS` in `RangeHTTPRequestHandler`**

Add these methods inside the class, right after `_handle_chat_api`:

```python
    def do_POST(self):
        """Route POST requests to API handlers."""
        if self.path == "/api/chat":
            self._handle_chat_api()
        else:
            self.send_error(404, "Not found")

    def do_OPTIONS(self):
        """Handle CORS preflight for API endpoints."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        SimpleHTTPRequestHandler.end_headers(self)
```

- [ ] **Step 4: Test the endpoint manually**

Start the server and test with curl:

```bash
# Start server
python static_server.py 8502 --directory static

# Test missing fields
curl -X POST http://localhost:8502/api/chat -H "Content-Type: application/json" -d '{}'
# Expected: 400 error

# Test missing subtitle file
curl -X POST http://localhost:8502/api/chat -H "Content-Type: application/json" -d '{"question":"test","stem":"nonexistent"}'
# Expected: 404 error

# Test valid request (should stream SSE)
curl -N -X POST http://localhost:8502/api/chat -H "Content-Type: application/json" -d '{"question":"What is ReLU?","stem":"2.02 - 1.1什么是神经网络(Av73508149,P2)_en"}'
# Expected: SSE stream with data: {"content": "..."} lines, ending with data: [DONE]
```

- [ ] **Step 5: Commit**

```bash
git add static_server.py
git commit -m "feat: add /api/chat endpoint for subtitle Q&A with SSE streaming"
```

---

### Task 2: Frontend — Create chatbot.js widget

**Files:**
- Create: `static/player/js/chatbot.js`

- [ ] **Step 1: Create the complete chatbot.js file**

Create `static/player/js/chatbot.js` with the following content:

```javascript
/**
 * Moogle Video Chatbot Widget
 * Floating bubble + panel for subtitle-based Q&A.
 * ES5-compatible to match existing codebase style.
 */
(function() {
    'use strict';

    // --- Configuration ---
    var PANEL_WIDTH = 360;
    var PANEL_HEIGHT = 480;
    var BUBBLE_SIZE = 48;
    var BUBBLE_MARGIN = 24;

    // --- State ---
    var isOpen = false;
    var isStreaming = false;
    var messages = [];
    var transcriptStem = '';

    // --- DOM References ---
    var bubble, panel, messagesEl, inputEl, sendBtn;

    // --- Initialize ---
    function init() {
        // Get transcript stem from URL params
        var params = new URLSearchParams(window.location.search);
        transcriptStem = params.get('stem') || '';
        if (!transcriptStem) {
            var src = params.get('src') || '';
            if (src) {
                transcriptStem = src.split('/').pop().replace(/\.[^.]+$/, '') + '_en';
            }
        }

        createBubble();
        createPanel();
        bindEvents();
    }

    // --- Create Bubble ---
    function createBubble() {
        bubble = document.createElement('div');
        bubble.id = 'moogle-chat-bubble';
        bubble.innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24" fill="#fff"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/><path d="M7 9h2v2H7zm4 0h2v2h-2zm4 0h2v2h-2z"/></svg>';
        bubble.title = 'Ask about this video';
        document.body.appendChild(bubble);
    }

    // --- Create Panel ---
    function createPanel() {
        panel = document.createElement('div');
        panel.id = 'moogle-chat-panel';
        panel.style.display = 'none';

        panel.innerHTML = ''
            + '<div class="moogle-chat-header">'
            +   '<span class="moogle-chat-title">💬 Ask about this video</span>'
            +   '<span class="moogle-chat-close" id="moogleChatClose">✕</span>'
            + '</div>'
            + '<div class="moogle-chat-messages" id="moogleChatMessages"></div>'
            + '<div class="moogle-chat-input-area">'
            +   '<input type="text" class="moogle-chat-input" id="moogleChatInput" placeholder="Ask about the subtitles..." autocomplete="off">'
            +   '<button class="moogle-chat-send" id="moogleChatSend">➤</button>'
            + '</div>';

        document.body.appendChild(panel);

        messagesEl = document.getElementById('moogleChatMessages');
        inputEl = document.getElementById('moogleChatInput');
        sendBtn = document.getElementById('moogleChatSend');
    }

    // --- Bind Events ---
    function bindEvents() {
        bubble.addEventListener('click', togglePanel);
        document.getElementById('moogleChatClose').addEventListener('click', togglePanel);

        sendBtn.addEventListener('click', sendMessage);
        inputEl.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
    }

    // --- Toggle Panel ---
    function togglePanel() {
        isOpen = !isOpen;
        if (isOpen) {
            panel.style.display = 'flex';
            // Force reflow for transition
            panel.offsetHeight;
            panel.classList.add('moogle-chat-open');
            bubble.classList.add('moogle-chat-bubble-active');
            inputEl.focus();
        } else {
            panel.classList.remove('moogle-chat-open');
            bubble.classList.remove('moogle-chat-bubble-active');
            setTimeout(function() {
                if (!isOpen) panel.style.display = 'none';
            }, 250);
        }
    }

    // --- Send Message ---
    function sendMessage() {
        var text = inputEl.value.trim();
        if (!text || isStreaming) return;

        inputEl.value = '';
        appendMessage('user', text);
        isStreaming = true;
        updateSendButton();

        // Create AI message placeholder with streaming cursor
        var aiMsgEl = appendMessage('ai', '');
        var cursorSpan = document.createElement('span');
        cursorSpan.className = 'moogle-chat-cursor';
        aiMsgEl.querySelector('.moogle-chat-bubble-text').appendChild(cursorSpan);

        // Call API
        fetchChat(text, aiMsgEl, cursorSpan);
    }

    // --- Fetch Chat (SSE) ---
    function fetchChat(question, msgEl, cursorSpan) {
        var url = '/api/chat';
        var payload = JSON.stringify({ question: question, stem: transcriptStem });

        var xhr = new XMLHttpRequest();
        xhr.open('POST', url, true);
        xhr.setRequestHeader('Content-Type', 'application/json');

        var buffer = '';
        var fullText = '';
        var textContainer = msgEl.querySelector('.moogle-chat-bubble-text');

        xhr.onprogress = function() {
            var newData = xhr.responseText.substring(buffer.length);
            buffer = xhr.responseText;

            var lines = newData.split('\n');
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (!line || !line.startsWith('data: ')) continue;
                var dataStr = line.substring(6);
                if (dataStr === '[DONE]') continue;
                try {
                    var chunk = JSON.parse(dataStr);
                    if (chunk.content) {
                        fullText += chunk.content;
                        renderStreamedText(textContainer, fullText, cursorSpan);
                    }
                } catch (e) {
                    // skip malformed chunks
                }
            }
            scrollToBottom();
        };

        xhr.onloadend = function() {
            isStreaming = false;
            updateSendButton();
            // Final render without cursor
            renderStreamedText(textContainer, fullText, null);
            scrollToBottom();
        };

        xhr.onerror = function() {
            isStreaming = false;
            updateSendButton();
            textContainer.textContent = 'Connection error. Please try again.';
            if (cursorSpan && cursorSpan.parentNode) {
                cursorSpan.parentNode.removeChild(cursorSpan);
            }
        };

        xhr.send(payload);
    }

    // --- Render Streamed Text with Timestamp Parsing ---
    function renderStreamedText(container, text, cursorSpan) {
        // Remove cursor temporarily
        if (cursorSpan && cursorSpan.parentNode) {
            cursorSpan.parentNode.removeChild(cursorSpan);
        }

        // Parse [ts:SECONDS] markers into clickable capsules
        var html = escapeHtml(text).replace(
            /\[ts:(\d+\.?\d*)\]/g,
            function(match, seconds) {
                var secs = parseFloat(seconds);
                var mins = Math.floor(secs / 60);
                var s = Math.floor(secs % 60);
                var timeStr = (mins < 10 ? '0' : '') + mins + ':' + (s < 10 ? '0' : '') + s;
                return '<span class="moogle-chat-timestamp" data-time="' + secs + '">⏱ ' + timeStr + '</span>';
            }
        );

        container.innerHTML = html;

        // Bind click events on timestamp capsules
        var stamps = container.querySelectorAll('.moogle-chat-timestamp');
        for (var i = 0; i < stamps.length; i++) {
            stamps[i].addEventListener('click', function() {
                var time = parseFloat(this.getAttribute('data-time'));
                seekVideo(time);
            });
        }

        // Re-append cursor if still streaming
        if (cursorSpan) {
            container.appendChild(cursorSpan);
        }
    }

    // --- Seek Video ---
    function seekVideo(seconds) {
        var video = document.getElementById('moogleVideo');
        if (!video) return;
        video.currentTime = seconds;
        video.play();
        // Optionally close panel so user can see the video
        if (isOpen) {
            togglePanel();
        }
    }

    // --- Append Message ---
    function appendMessage(role, text) {
        var wrapper = document.createElement('div');
        wrapper.className = 'moogle-chat-msg moogle-chat-msg-' + role;

        var bubbleDiv = document.createElement('div');
        bubbleDiv.className = 'moogle-chat-bubble-text';
        if (role === 'user') {
            bubbleDiv.textContent = text;
        }
        // AI messages start empty and get filled by streaming

        wrapper.appendChild(bubbleDiv);
        messagesEl.appendChild(wrapper);
        scrollToBottom();

        return wrapper;
    }

    // --- Helpers ---
    function scrollToBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function updateSendButton() {
        sendBtn.disabled = isStreaming;
        sendBtn.style.opacity = isStreaming ? '0.5' : '1';
    }

    function escapeHtml(str) {
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    // --- Inject Styles ---
    function injectStyles() {
        var css = ''
            + '#moogle-chat-bubble {'
            + '  position: fixed; bottom: ' + BUBBLE_MARGIN + 'px; right: ' + BUBBLE_MARGIN + 'px;'
            + '  width: ' + BUBBLE_SIZE + 'px; height: ' + BUBBLE_SIZE + 'px;'
            + '  background: #856729; border-radius: 50%; cursor: pointer;'
            + '  display: flex; align-items: center; justify-content: center;'
            + '  box-shadow: 0 2px 10px rgba(0,0,0,0.4); z-index: 1003;'
            + '  transition: background 0.2s, transform 0.2s;'
            + '}'
            + '#moogle-chat-bubble:hover { background: #a07d35; transform: scale(1.08); }'
            + '#moogle-chat-bubble.moogle-chat-bubble-active { background: #5a471c; }'
            + '#moogle-chat-bubble svg { pointer-events: none; }'
            + ''
            + '#moogle-chat-panel {'
            + '  position: fixed; bottom: ' + (BUBBLE_MARGIN + BUBBLE_SIZE + 12) + 'px; right: ' + BUBBLE_MARGIN + 'px;'
            + '  width: ' + PANEL_WIDTH + 'px; height: ' + PANEL_HEIGHT + 'px; max-height: 60vh;'
            + '  background: rgba(26, 45, 75, 0.95);'
            + '  border: 1px solid rgba(255,255,255,0.3); border-radius: 8px;'
            + '  box-shadow: 0 4px 20px rgba(0,0,0,0.4); z-index: 1003;'
            + '  display: flex; flex-direction: column;'
            + '  opacity: 0; transform: translateY(20px);'
            + '  transition: opacity 0.25s ease, transform 0.25s ease;'
            + '}'
            + '#moogle-chat-panel.moogle-chat-open { opacity: 1; transform: translateY(0); }'
            + ''
            + '.moogle-chat-header {'
            + '  background: #856729; padding: 10px 14px;'
            + '  border-radius: 8px 8px 0 0; display: flex;'
            + '  justify-content: space-between; align-items: center;'
            + '}'
            + '.moogle-chat-title { color: #fff; font-size: 13px; font-weight: 600; font-family: "Open Sans", sans-serif; }'
            + '.moogle-chat-close { color: rgba(255,255,255,0.7); font-size: 14px; cursor: pointer; padding: 2px 6px; }'
            + '.moogle-chat-close:hover { color: #fff; }'
            + ''
            + '.moogle-chat-messages {'
            + '  flex: 1; overflow-y: auto; padding: 12px;'
            + '  display: flex; flex-direction: column; gap: 8px;'
            + '}'
            + ''
            + '.moogle-chat-msg { max-width: 85%; }'
            + '.moogle-chat-msg-user { align-self: flex-end; }'
            + '.moogle-chat-msg-ai { align-self: flex-start; }'
            + ''
            + '.moogle-chat-bubble-text {'
            + '  padding: 8px 12px; font-size: 13px; line-height: 1.6;'
            + '  font-family: "Open Sans", sans-serif; word-wrap: break-word;'
            + '}'
            + '.moogle-chat-msg-user .moogle-chat-bubble-text {'
            + '  background: #856729; color: #fff; border-radius: 8px 8px 0 8px;'
            + '}'
            + '.moogle-chat-msg-ai .moogle-chat-bubble-text {'
            + '  background: #2A3B55; color: #ddd; border-radius: 8px 8px 8px 0;'
            + '  border: 1px solid rgba(255,255,255,0.1);'
            + '}'
            + ''
            + '.moogle-chat-timestamp {'
            + '  display: inline-block; background: rgba(240,192,64,0.15);'
            + '  color: #f0c040; padding: 1px 6px; border-radius: 8px;'
            + '  font-size: 0.8em; cursor: pointer; margin: 0 2px;'
            + '  white-space: nowrap; transition: background 0.15s;'
            + '}'
            + '.moogle-chat-timestamp:hover { background: rgba(240,192,64,0.3); }'
            + ''
            + '.moogle-chat-cursor {'
            + '  display: inline-block; width: 4px; height: 14px;'
            + '  background: #f0c040; vertical-align: middle; margin-left: 2px;'
            + '  animation: moogle-blink 0.8s step-end infinite;'
            + '}'
            + '@keyframes moogle-blink { 50% { opacity: 0; } }'
            + ''
            + '.moogle-chat-input-area {'
            + '  padding: 8px; border-top: 1px solid rgba(255,255,255,0.15);'
            + '  display: flex; gap: 6px; border-radius: 0 0 8px 8px;'
            + '}'
            + '.moogle-chat-input {'
            + '  flex: 1; background: #1A2D4B; border: 1px solid rgba(255,255,255,0.3);'
            + '  padding: 8px 12px; border-radius: 4px; color: #fff;'
            + '  font-size: 13px; font-family: "Open Sans", sans-serif; outline: none;'
            + '}'
            + '.moogle-chat-input::placeholder { color: rgba(255,255,255,0.4); }'
            + '.moogle-chat-input:focus { border-color: #856729; }'
            + '.moogle-chat-send {'
            + '  background: #856729; border: none; color: #fff;'
            + '  padding: 8px 14px; border-radius: 4px; cursor: pointer;'
            + '  font-size: 14px; transition: background 0.2s;'
            + '}'
            + '.moogle-chat-send:hover { background: #a07d35; }'
            + '.moogle-chat-send:disabled { opacity: 0.5; cursor: default; }'
            + ''
            + '@media (max-width: 480px) {'
            + '  #moogle-chat-panel {'
            + '    width: calc(100vw - 32px); right: 16px;'
            + '    bottom: ' + (BUBBLE_MARGIN + BUBBLE_SIZE + 8) + 'px;'
            + '    max-height: 50vh;'
            + '  }'
            + '}';

        var style = document.createElement('style');
        style.textContent = css;
        document.head.appendChild(style);
    }

    // --- Boot ---
    injectStyles();
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
```

- [ ] **Step 2: Verify file was created correctly**

Run: check that `static/player/js/chatbot.js` exists and has ~300 lines.

- [ ] **Step 3: Commit**

```bash
git add static/player/js/chatbot.js
git commit -m "feat: add chatbot widget with floating bubble, panel, SSE streaming, and timestamp capsules"
```

---

### Task 3: Integration — Wire chatbot into player.html

**Files:**
- Modify: `static/player/player.html`

- [ ] **Step 1: Add chatbot script tag**

In `static/player/player.html`, find line 326:
```html
<script src="js/jquery.magnific-popup.min.js"></script>
```

Add immediately after it (before the inline `<script>` block):
```html
<script src="js/chatbot.js"></script>
```

- [ ] **Step 2: Verify integration**

Start the server and open the player page in a browser:
```bash
python static_server.py 8502 --directory static
```

Navigate to: `http://localhost:8502/player/player.html?src=/video/test.mp4&stem=2.02+-+1.1什么是神经网络(Av73508149,P2)_en`

Verify:
1. Gold chat bubble appears in bottom-right corner
2. Clicking it opens the panel with smooth animation
3. Typing a question and pressing Enter sends the request
4. AI response streams in with blinking cursor
5. Timestamp capsules appear in gold and are clickable
6. Clicking a timestamp seeks the video and closes the panel
7. Close button (✕) works
8. Panel is responsive on narrow viewports

- [ ] **Step 3: Commit**

```bash
git add static/player/player.html
git commit -m "feat: integrate chatbot widget into video player page"
```

---

### Task 4: End-to-end verification

**Files:** None (testing only)

- [ ] **Step 1: Test with real subtitle file**

Use the existing subtitle file to verify the full flow:

```bash
# Start server
python static_server.py 8502 --directory static
```

Open player with the neural network video. Ask "What is ReLU?" and verify:
- Response mentions ReLU definition
- At least one `[ts:...]` marker appears as a gold capsule
- Clicking the capsule jumps to approximately 01:04

- [ ] **Step 2: Test error cases**

- Ask a question with no stem parameter → should show error gracefully
- Stop the server mid-stream → should show connection error
- Ask in Chinese → response should be in Chinese

- [ ] **Step 3: Test responsive behavior**

Resize browser to < 480px width. Verify panel goes full-width and doesn't overflow.

- [ ] **Step 4: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix: chatbot edge case fixes from e2e testing"
```
