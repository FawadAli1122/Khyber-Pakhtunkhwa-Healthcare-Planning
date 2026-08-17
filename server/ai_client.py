"""Real chat/message-generation calls to all five AI providers, used by the
"Ask AI" chat panel. Unlike phase 2's providers.test_key() (which only
lists models), this makes an actual answer-generating call. See
docs/superpowers/specs/2026-08-15-ai-chat-panel-phase3-design.md
section 4.
"""
import anthropic
import requests

REQUEST_TIMEOUT_SECONDS = 30
MAX_ANSWER_TOKENS = 1024

SYSTEM_PROMPT = (
    "You are a healthcare planning assistant. Answer using only the data "
    "digest provided; if the digest doesn't contain the answer, say so "
    "plainly rather than guessing."
)

# One named constant per provider - trivially updated later as provider
# lineups change. Anthropic's is the current default per this project's
# established Claude tooling conventions.
MODELS = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "grok": "grok-2-latest",
    "groq": "llama-3.3-70b-versatile",
}


class AIProviderError(Exception):
    """Raised when a provider call fails, carrying a message safe to show
    the user directly - never a raw traceback."""


def _ask_anthropic(key, question, context):
    try:
        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model=MODELS["anthropic"],
            max_tokens=MAX_ANSWER_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"{context}\n\nQuestion: {question}"}],
        )
        text_blocks = [block.text for block in response.content if block.type == "text"]
        return "".join(text_blocks).strip() or "(no answer returned)"
    except Exception as exc:
        raise AIProviderError(f"Claude request failed: {exc}") from exc


def _ask_openai_style(url, model, headers_fn):
    def _ask(key, question, context):
        try:
            response = requests.post(
                url,
                headers=headers_fn(key),
                json={
                    "model": model,
                    "max_tokens": MAX_ANSWER_TOKENS,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
                    ],
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise AIProviderError(f"Request failed: {exc}") from exc
        if response.status_code != 200:
            raise AIProviderError(f"Provider returned HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as exc:
            raise AIProviderError(f"Unexpected response shape: {exc}") from exc
    return _ask


def _ask_gemini(key, question, context):
    url = f"https://generativelanguage.googleapis.com/v1/models/{MODELS['gemini']}:generateContent"
    try:
        response = requests.post(
            url,
            params={"key": key},
            json={
                "contents": [{"parts": [{"text": f"{SYSTEM_PROMPT}\n\n{context}\n\nQuestion: {question}"}]}],
                "generationConfig": {"maxOutputTokens": MAX_ANSWER_TOKENS},
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise AIProviderError(f"Request failed: {exc}") from exc
    if response.status_code != 200:
        raise AIProviderError(f"Provider returned HTTP {response.status_code}: {response.text[:200]}")
    data = response.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts).strip()
    except (KeyError, IndexError) as exc:
        raise AIProviderError(f"Unexpected response shape: {exc}") from exc


_ASKERS = {
    "anthropic": _ask_anthropic,
    "openai": _ask_openai_style(
        "https://api.openai.com/v1/chat/completions",
        MODELS["openai"],
        lambda key: {"Authorization": f"Bearer {key}"},
    ),
    "gemini": _ask_gemini,
    "grok": _ask_openai_style(
        "https://api.x.ai/v1/chat/completions",
        MODELS["grok"],
        lambda key: {"Authorization": f"Bearer {key}"},
    ),
    "groq": _ask_openai_style(
        "https://api.groq.com/openai/v1/chat/completions",
        MODELS["groq"],
        lambda key: {"Authorization": f"Bearer {key}"},
    ),
}


def ask(provider, key, question, context):
    asker = _ASKERS.get(provider)
    if asker is None:
        raise AIProviderError(f"Unknown provider: {provider}")
    if not key:
        raise AIProviderError("No API key configured for this provider")
    if not question or not question.strip():
        raise AIProviderError("Question must not be empty")
    return asker(key, question, context)
