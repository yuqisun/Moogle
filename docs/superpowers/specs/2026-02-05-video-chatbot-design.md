# Video Player Chatbot Design Spec

## Overview

Add a subtitle-based Q&A chatbot to the video player page (`player.html`). Users can ask questions about the current video's subtitles and receive AI-powered answers with inline timestamp citations that jump to the relevant moment in the video.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Layout | Floating bubble (bottom-right) | Doesn't consume layout space; familiar pattern; always accessible |
| Scope | Current video subtitles only | Focused context; avoids redundancy with Streamlit search |
| Backend | Existing DeepSeek LLM API | Reuse `.env` config; consistent with Streamlit experience |
| Visual style | Dark theme match | `#1A2D4B` panel bg, `#856729` user bubbles, `#2A3B55` AI bubbles |
| Timestamps | Inline gold capsule tags | Precise citation placement; natural reading flow; supports multiple refs |
| API hosting | Extend `static_server.py` | Minimal change; no new process; same port avoids CORS |

## Architecture

### Data Flow

```
User types question → Frontend POST /api/chat
  → Server loads subtitle JSON (/txt/{stem}.json)
  → Server builds prompt with full subtitle text as context
  → Server streams LLM response via SSE
  → Frontend renders tokens incrementally
  → Frontend parses [ts:XX.XX] markers into clickable capsules
  → Click on capsule → video.currentTime = XX.XX; video.play()
```

### Components

#### 1. Frontend: Chatbot Widget (`chatbot.js` + inline CSS)

New file: `static/player/js/chatbot.js`

**Floating Bubble:**
- Fixed position: `bottom: 24px; right: 24px`
- Circle button: 48px diameter, `background: #856729`, white chat icon
- Hover: `background: #a07d35`, subtle scale transform
- z-index: 1003 (above nav at 1002)
- When panel is open, bubble transforms to ✕ close icon

**Chat Panel:**
- Fixed position: `bottom: 84px; right: 24px`
- Dimensions: `width: 360px; height: 480px` (max-height: 60vh on small screens)
- Background: `rgba(26, 45, 75, 0.95)`
- Border: `1px solid rgba(255,255,255,0.3)`
- Border-radius: `8px`
- Box-shadow: `0 4px 20px rgba(0,0,0,0.4)`
- Open/close transition: `transform: translateY(20px); opacity: 0` → visible, 0.25s ease
- Header: `background: #856729`, white title "💬 Ask about this video", close button
- Messages area: scrollable, auto-scroll to bottom on new message
- Input area: dark input field + gold send button, matching existing button styles

**Message Bubbles:**
- AI: `background: #2A3B55`, `border-radius: 8px 8px 8px 0`, left-aligned
- User: `background: #856729`, `border-radius: 8px 8px 0 8px`, right-aligned
- Font: Open Sans 14px, line-height 1.6, color white/light gray
- Max-width: 85% of panel

**Timestamp Capsules:**
- Inline `<span>` with `background: rgba(240,192,64,0.15)`, `color: #f0c040`
- Padding: `1px 6px`, border-radius: `8px`, font-size: `0.8em`
- Cursor: pointer, hover brightens background
- Click handler: seek video to timestamp, play, optionally highlight current caption

**Streaming Indicator:**
- Gold blinking cursor block (`#f0c040`, 4px wide) appended during streaming
- Removed when stream completes

**Responsive Behavior:**
- Screen width < 480px: panel goes full-width minus margins, bottom-aligned
- Panel position adjusts to avoid overlapping video controls

#### 2. Backend: `/api/chat` Endpoint

Extend `static_server.py` with a new request handler.

**Route:** `POST /api/chat`

**Request Body:**
```json
{
  "question": "What is ReLU?",
  "stem": "2.02 - 1.1什么是神经网络(Av73508149,P2)_en"
}
```

**Processing:**
1. Load `/txt/{stem}.json` from the static directory
2. Extract full text from the `text` field (or concatenate `segments[].text`)
3. Build prompt:
   ```
   User question: {question}

   The following is the complete subtitle transcript of the current video:
   {full_text}

   Please answer the user's question based on this transcript.
   When referencing specific content, insert timestamp markers in the format [ts:SECONDS] 
   where SECONDS is the exact start time from the segments.
   Reply in the same language as the question. Be concise.
   ```
4. Call DeepSeek API (streaming) using config from `.env`
5. Stream SSE response back to client

**Response:** SSE stream (`text/event-stream`)
```
data: {"content": "ReLU is"}
data: {"content": " an activation"}
data: {"content": " function [ts:64.5]"}
data: {"content": " that..."}
data: [DONE]
```

**Error Handling:**
- Missing stem or file not found → `{"error": "Subtitle not found"}`
- No API key configured → fallback template response listing relevant segments
- LLM timeout/error → error message in stream

#### 3. Integration Points

**In `player.html`:**
- Add `<script src="js/chatbot.js"></script>` after existing scripts
- Pass `transcriptStem` to chatbot init (already available in the IIFE scope)
- Chatbot reads `stem` from URL params independently as fallback

**Subtitle Segment Lookup:**
The server needs segment-level timestamps for citation. The existing `{stem}.json` files contain a `segments` array with `{start, end, text}` objects. The prompt instructs the LLM to emit `[ts:SECONDS]` markers. The frontend regex-parses these into clickable capsules.

If the LLM emits a timestamp not exactly matching a segment start, the frontend finds the nearest segment (within ±2s tolerance) and uses its exact start time.

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `static/player/js/chatbot.js` | **Create** | Chatbot widget: bubble, panel, messaging, streaming, timestamp parsing |
| `static_server.py` | **Modify** | Add `/api/chat` POST handler with SSE streaming |
| `static/player/player.html` | **Modify** | Add chatbot script tag; pass stem param |
| `.env.example` | **No change** | Already has LLM_API_KEY, LLM_API_BASE, LLM_MODEL |

## Non-Goals

- Cross-video search (handled by Streamlit homepage)
- Chat history persistence (session-only, resets on page refresh)
- Multi-turn conversation memory (each question includes full subtitle context)
- Voice input/output
- Markdown rendering in responses (plain text with timestamp markers only)

## Testing Plan

1. **Unit:** Timestamp marker parsing regex handles edge cases (multiple markers, malformed markers, no markers)
2. **Integration:** `/api/chat` returns valid SSE stream with correct subtitle context
3. **Visual:** Panel opens/closes smoothly; messages scroll correctly; timestamps are clickable and seek accurately
4. **Responsive:** Panel adapts on narrow screens; doesn't overlap video controls
5. **Error:** Graceful handling when subtitle file missing, API key absent, LLM fails
