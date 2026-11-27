"""Verify the OpenRouter configuration end to end:   python scripts/check_openrouter.py   (run from backend/)

Uses the application's own settings (.env) and provider class, so a pass here means the app will reach OpenRouter the same way. Prints the model, the reply
and the token usage; never prints the key."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.providers.openrouter import OpenRouterProvider  # noqa: E402
from app.ai.types import GenerationParams  # noqa: E402
from app.core.config import get_settings  # noqa: E402


async def main() -> int:
    s = get_settings()
    if not s.OPENROUTER_API_KEY.strip():
        print("\n FAILED: OPENROUTER_API_KEY not found in .env (add it, then add 'openrouter' to LLM_PROVIDERS)")
        return 2
    p = OpenRouterProvider(s.OPENROUTER_MODEL, s.OPENROUTER_API_KEY.strip(), s.OPENROUTER_BASE_URL, s.OPENROUTER_REFERER, s.OPENROUTER_TITLE)
    try:
        r = await p.generate("Say hello and confirm that OpenRouter API is working.", GenerationParams(max_output_tokens=100, timeout_s=60))
    except Exception as e:  # noqa: BLE001
        print("\n FAILED: OpenRouter API request failed")
        print("Error:", getattr(e, "message", str(e)))
        return 1
    print("\n SUCCESS: OpenRouter API is working!")
    print("Model:", r.model)
    print("Response:", r.text)
    print("\nToken Usage:\nPrompt tokens:", r.usage.prompt_tokens, "\nCompletion tokens:", r.usage.completion_tokens, "\nTotal tokens:", r.usage.total)
    print("\nIn LLM_PROVIDERS:", "yes" if "openrouter" in s.LLM_PROVIDERS.lower() else "NO - add 'openrouter' so the app uses it")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
