"""HTML/CSS/JS for the "Ask AI" chat panel, injected into the served
dashboard HTML at serve-time (scripts/14_build_html_report.py's static
output is never modified - see docs/superpowers/specs/
2026-08-15-ai-chat-panel-phase3-design.md section 3). Same string-constant
pattern as admin_ui.py.
"""

PROVIDER_OPTIONS = (
    ("anthropic", "Claude (Anthropic)"),
    ("openai", "OpenAI"),
    ("gemini", "Gemini"),
    ("grok", "Grok (xAI)"),
    ("groq", "Groq"),
)

CHAT_CSS = r"""
#ai-chat-toggle {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  z-index: 1000;
  background: var(--accent, #a85a17);
  color: #fff;
  border: none;
  border-radius: 999px;
  padding: 0.75rem 1.25rem;
  font-size: 0.9rem;
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(0,0,0,0.2);
}
#ai-chat-panel {
  position: fixed;
  bottom: 5rem;
  right: 1.5rem;
  z-index: 1000;
  width: min(380px, calc(100vw - 3rem));
  max-height: min(560px, calc(100vh - 8rem));
  display: none;
  flex-direction: column;
  background: var(--panel, #fff);
  border: 1px solid var(--line, rgba(22,33,31,0.13));
  border-radius: 10px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.25);
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
  color: var(--ink, #16211f);
  overflow: hidden;
}
#ai-chat-panel.open { display: flex; }
#ai-chat-header {
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--line, rgba(22,33,31,0.13));
  font-weight: 600;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
#ai-chat-close { background: none; border: none; cursor: pointer; font-size: 1rem; color: var(--muted, #7c8580); }
#ai-chat-log {
  flex: 1;
  overflow-y: auto;
  padding: 0.75rem 1rem;
  font-size: 0.85rem;
}
.ai-chat-entry { margin-bottom: 0.9rem; }
.ai-chat-question { font-weight: 600; margin-bottom: 0.25rem; }
.ai-chat-answer { white-space: pre-wrap; color: var(--ink-soft, #48534f); }
.ai-chat-answer.error { color: var(--danger, #b3392b); }
.ai-chat-answer.pending { color: var(--muted, #7c8580); font-style: italic; }
#ai-chat-form {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border-top: 1px solid var(--line, rgba(22,33,31,0.13));
}
#ai-chat-provider, #ai-chat-input {
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--line, rgba(22,33,31,0.13));
  border-radius: 6px;
  background: var(--paper, #f3f6f4);
  color: var(--ink, #16211f);
  font-size: 0.85rem;
  font-family: inherit;
}
#ai-chat-input { resize: vertical; min-height: 2.5rem; }
#ai-chat-submit {
  background: var(--accent, #a85a17);
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 0.5rem;
  font-size: 0.85rem;
  cursor: pointer;
}
"""

CHAT_JS = r"""
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.getElementById("ai-chat-toggle");
    var panel = document.getElementById("ai-chat-panel");
    var closeBtn = document.getElementById("ai-chat-close");
    var form = document.getElementById("ai-chat-form");
    var providerSelect = document.getElementById("ai-chat-provider");
    var input = document.getElementById("ai-chat-input");
    var log = document.getElementById("ai-chat-log");

    if (!toggle || !panel || !form) return;

    toggle.addEventListener("click", function () {
      panel.classList.toggle("open");
    });
    closeBtn.addEventListener("click", function () {
      panel.classList.remove("open");
    });

    form.addEventListener("submit", function (evt) {
      evt.preventDefault();
      var question = input.value.trim();
      if (!question) return;

      var entry = document.createElement("div");
      entry.className = "ai-chat-entry";
      var questionEl = document.createElement("div");
      questionEl.className = "ai-chat-question";
      questionEl.textContent = question;
      var answerEl = document.createElement("div");
      answerEl.className = "ai-chat-answer pending";
      answerEl.textContent = "Thinking...";
      entry.appendChild(questionEl);
      entry.appendChild(answerEl);
      log.appendChild(entry);
      log.scrollTop = log.scrollHeight;
      input.value = "";

      fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: providerSelect.value, question: question }),
      })
        .then(function (res) {
          return res.json().then(function (data) { return { ok: res.ok, data: data }; });
        })
        .then(function (result) {
          answerEl.classList.remove("pending");
          if (result.ok) {
            answerEl.textContent = result.data.answer;
          } else {
            answerEl.classList.add("error");
            answerEl.textContent = (result.data && result.data.detail) || "Request failed";
          }
          log.scrollTop = log.scrollHeight;
        })
        .catch(function (err) {
          answerEl.classList.remove("pending");
          answerEl.classList.add("error");
          answerEl.textContent = "Request failed: " + err;
        });
    });
  });
})();
"""


def render_widget():
    options = "\n".join(f'    <option value="{value}">{label}</option>' for value, label in PROVIDER_OPTIONS)
    return f"""<style>{CHAT_CSS}</style>
<button type="button" id="ai-chat-toggle">Ask AI</button>
<div id="ai-chat-panel">
  <div id="ai-chat-header">
    <span>Ask AI about this plan</span>
    <button type="button" id="ai-chat-close" aria-label="Close">&times;</button>
  </div>
  <div id="ai-chat-log"></div>
  <form id="ai-chat-form">
    <select id="ai-chat-provider">
{options}
    </select>
    <textarea id="ai-chat-input" placeholder="Ask a question about the plan..." required></textarea>
    <button type="submit" id="ai-chat-submit">Ask</button>
  </form>
</div>
<script>{CHAT_JS}</script>
"""
