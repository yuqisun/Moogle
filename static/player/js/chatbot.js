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
    var transcriptStem = '';

    // --- DOM References ---
    var bubble, panel, messagesEl, inputEl, sendBtn;

    // --- Initialize ---
    function init() {
        // Get transcript stem from URL params (new 's' or legacy 'stem')
        var params = new URLSearchParams(window.location.search);
        transcriptStem = params.get('s') || params.get('stem') || '';
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
            +   '<input type="text" class="moogle-chat-input" id="moogleChatInput" placeholder="Ask about this video..." autocomplete="off">'
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
            panel.offsetHeight; // eslint-disable-line no-unused-expressions
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

    // --- Fetch Chat (SSE via XHR) ---
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
        // Close panel so user can see the video
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
