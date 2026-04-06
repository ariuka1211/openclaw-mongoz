"""Token estimation — tiktoken with fallback to char//4."""
import logging

log = logging.getLogger("ai-trader.context.tokens")

try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")

    def estimate_tokens(text: str) -> int:
        """Estimate token count using cl100k_base encoding."""
        return len(_enc.encode(text))

except ImportError:
    log.warning("tiktoken not installed — falling back to char//4 token estimation")

    def estimate_tokens(text: str) -> int:
        """Fallback token estimation: ~4 chars per token."""
        return len(text) // 4


MAX_PROMPT_TOKENS = 16000
