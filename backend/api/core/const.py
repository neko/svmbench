# Models are fetched dynamically from Daydreams router
# See api/util/pricing.py:get_x402_models()
#
# Model format: provider:model-id (e.g., anthropic:claude-sonnet-4-6)

# Legacy constants for backwards compatibility
ALLOWED_MODELS: set[str] = set()
OPENROUTER_ALLOWED_MODELS: set[str] = set()
ALLOWED_PROVIDERS = {'daydreams'}
