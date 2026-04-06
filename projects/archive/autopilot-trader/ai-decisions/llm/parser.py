"""LLM response parsing — extract decision JSON from raw LLM output."""
import json
import logging

log = logging.getLogger("ai-trader.parser")


def parse_decision_json(raw: str) -> dict:
    """Parse LLM response into a decision dict. Handles markdown code blocks and extra text."""
    text = raw.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    # Find the JSON object using brace-depth tracking
    start = text.find("{")
    if start == -1:
        log.error(f"No JSON object found in LLM response: {raw[:300]}")
        return {"action": "hold", "reasoning": "No JSON found in response", "confidence": 0}

    depth = 0
    end = None
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end is None:
        log.error(f"Unmatched braces in LLM response: {raw[:300]}")
        return {"action": "hold", "reasoning": "Unmatched JSON braces", "confidence": 0}

    json_text = text[start:end]

    try:
        decision = json.loads(json_text)
        decision.pop("leverage", None)  # Strip stale field — position sizing is USD-based now
        return decision
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse LLM JSON: {e}\nRaw: {raw[:500]}")
        return {"action": "hold", "reasoning": f"JSON parse error: {e}", "confidence": 0}
