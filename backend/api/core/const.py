ALLOWED_MODELS = {
    'codex-gpt-5.1-codex-max',
    'codex-gpt-5.2',
}

OPENROUTER_ALLOWED_MODELS = {
    'minimax/minimax-m2.5',
    'moonshotai/kimi-k2.5',
    'z-ai/glm-5',
    'google/gemini-3-flash-preview',
    'deepseek/deepseek-v3.2',
    'anthropic/claude-opus-4.6',
    'anthropic/claude-opus-4.5',
    'x-ai/grok-4.1-fast',
    'openai/gpt-5.2-codex',
    'openai/gpt-5.1-codex-max',
}

# x402 models are fetched dynamically from x402 discovery endpoint
# See api/util/pricing.py:get_x402_models()

ALLOWED_PROVIDERS = {'openai', 'openrouter', 'x402'}
