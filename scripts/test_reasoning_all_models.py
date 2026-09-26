#!/usr/bin/env python3
"""
Test what happens when we send reasoning={"enabled": true} to every OpenRouter model.

Uses data/settings.json: openrouter_api_key and council_models (OpenRouter entries only).
Run from project root: uv run python scripts/test_reasoning_all_models.py

Limits to first MAX_MODELS to avoid long runs; set to None to test all.
"""

import json
import os
import sys

# Run from project root so backend is importable
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import httpx

MAX_MODELS = 6  # Cap so the script finishes in reasonable time; None = all
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT = 60.0


def main():
    from backend.settings import get_settings

    settings = get_settings()
    api_key = settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("No OpenRouter API key in settings or OPENROUTER_API_KEY env. Exiting.")
        sys.exit(1)

    # Only OpenRouter models from council_models
    council = settings.council_models or []
    openrouter_models = [
        m.removeprefix("openrouter:")
        for m in council
        if m and m.startswith("openrouter:")
    ]
    if not openrouter_models:
        # Fallback: a few known models to test
        openrouter_models = [
            "openai/gpt-4o-mini",
            "google/gemini-2.5-flash",
            "deepseek/deepseek-v3.2",
            "anthropic/claude-3.5-haiku",
        ]
    to_test = openrouter_models[:MAX_MODELS] if MAX_MODELS else openrouter_models

    print(f"Testing reasoning={{enabled: true}} on {len(to_test)} model(s) from settings.")
    print("(Using council_models from data/settings.json)\n")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload_base = {
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 50,
        "reasoning": {"enabled": True},
    }

    results = []
    for model in to_test:
        payload = {**payload_base, "model": model}
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                r = client.post(OPENROUTER_URL, headers=headers, json=payload)
        except Exception as e:
            results.append((model, "EXCEPTION", str(e)))
            continue

        if r.status_code == 200:
            data = r.json()
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            content = (msg.get("content") or "").strip()[:80]
            reasoning = "yes" if msg.get("reasoning") or msg.get("reasoning_details") else "no"
            results.append((model, "OK", f"content_len={len(msg.get('content') or '')} reasoning={reasoning}"))
        else:
            try:
                err = r.json()
                err_msg = err.get("error", {}).get("message", r.text) if isinstance(err.get("error"), dict) else r.text
            except Exception:
                err_msg = r.text
            err_preview = (err_msg or str(r.status_code))[:200]
            results.append((model, "ERROR", f"{r.status_code} {err_preview}"))

    # Report
    print(f"{'Model':<50} {'Status':<8} Details")
    print("-" * 90)
    for model, status, detail in results:
        print(f"{model:<50} {status:<8} {detail}")
    print()
    ok = sum(1 for _, s, _ in results if s == "OK")
    print(f"Summary: {ok}/{len(results)} succeeded with reasoning={{enabled: true}}.")


if __name__ == "__main__":
    main()
