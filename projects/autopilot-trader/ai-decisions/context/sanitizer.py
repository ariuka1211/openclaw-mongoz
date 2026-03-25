"""Prompt injection sanitizer — detect and strip injection attempts from LLM/user text."""
import re
import logging

log = logging.getLogger("ai-trader.context.sanitizer")

_INJECTION_PATTERNS = [
    r'\bignore\s+(all\s+)?previous\b',
    r'\bignore\s+(all\s+)?above\b',
    r'\bignore\s+(all\s+)?instructions\b',
    r'\bdisregard\s+(all\s+)?previous\b',
    r'\bdisregard\s+(all\s+)?above\b',
    r'\bdisregard\s+(all\s+)?instructions\b',
    r'\boverride\s+(your\s+)?instructions\b',
    r'\boverride\s+(the\s+)?system\b',
    r'\bforget\s+(all\s+)?previous\b',
    r'\bforget\s+(all\s+)?instructions\b',
    r'\bact\s+as\s+(a\s+)?different\b',
    r'\bnew\s+instructions\b',
    r'\bsystem\s*prompt\b',
    r'\bsystem\s*message\b',
    r'\bjailbreak\b',
    r'\bdan\s*mode\b',
    r'\bunfiltered\s*(mode)?\b',
    r'\bimportant\s*:?\s*instruction\b',
    r'\breplace\s+(the\s+)?system\b',
    r'\bdisable\s+(safety|filter)\b',
    r'\boverride\s+safety\b',
]


def strip_injection_patterns(text: str) -> str:
    """Remove prompt-injection attempts from text using regex with word boundaries.

    When an injection pattern is found, truncates the text at the match point
    (everything after the injection is removed since it may contain malicious instructions).
    Iterates to handle multiple injection attempts. Returns [blocked] if entire text is consumed.
    """
    if not text:
        return ""
    for _ in range(10):  # max 10 passes to prevent infinite loops
        first_match = None
        for pattern in _INJECTION_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m and (first_match is None or m.start() < first_match.start()):
                first_match = m
        if first_match is None:
            break
        text = text[:first_match.start()].strip()
    if not text:
        return "[blocked]"
    return text


def sanitize_reasoning(text: str | None) -> str:
    """Truncate and strip prompt-injection attempts from LLM reasoning."""
    if not text:
        return ""
    s = text[:200]
    return strip_injection_patterns(s)
