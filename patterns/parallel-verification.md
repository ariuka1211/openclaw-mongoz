# Parallel Verification Pattern

**Inspired by:** Grok 4.20's multi-agent debate architecture
**Added:** 2026-03-20

## Problem

Current flow is sequential: Agent A researches → Maaraa reviews → Agent B builds.
If Agent A has a blind spot or hallucinates, the error propagates silently.

## Solution: Parallel Debate → Synthesis

### Pattern 1: Research Verification (simple)

For high-stakes research questions, spawn two subagents in parallel:

```
1. Spawn Blitz-A with the research question
2. Spawn Blitz-B with the SAME question + "challenge the premise and find flaws"
3. Wait for both to complete
4. Maaraa synthesizes: compare answers, flag disagreements, merge findings
5. Final result = consensus + noted disagreements
```

**When to use:**
- API documentation interpretation
- Market analysis before trades
- Technical architecture decisions
- Any research that leads to action

**When NOT to use:**
- Simple factual lookups
- Time-sensitive decisions (parallel = 2x latency)
- Tasks where error cost is near-zero

### Pattern 2: Build Verification (medium)

For implementation tasks, add a review step:

```
1. Spawn Mzinho to implement feature
2. On completion, spawn a separate review session with the task:
   "Review this code for bugs, security issues, and edge cases.
    Code at: [path]. Report any problems found."
3. If issues found → send back to Mzinho with findings
4. If clean → declare done
```

**When to use:**
- Security-sensitive code (auth, crypto, API keys)
- Trading bot logic (money at stake)
- Infrastructure changes

**When NOT to use:**
- Simple file edits
- Config changes
- Quick scripts

### Pattern 3: Adversarial Verification (full debate)

For critical decisions, run true parallel debate:

```
1. Spawn Agent-A: "Research X and give your recommendation"
2. Spawn Agent-B: "Research X. Play devil's advocate — find what's wrong with [premise]"
3. Collect both results
4. Maaraa reconciles: present best case for each side, then synthesis
```

**When to use:**
- Strategic decisions (should we switch exchanges?)
- Large changes with rollback cost
- Anything involving money

## Implementation: Maaraa's Verdict Protocol

After any subagent returns with important findings:

1. **Quick check** (Maaraa does this, no spawn): Does the output smell right? Sources cited? Numbers add up?
2. **If suspicious or high-stakes**: Spawn a verifier with the output and ask "find problems with this analysis"
3. **If conflicting**: Present both views to John with a recommendation

### Spawn Template for Verification

```
You are a critical reviewer. Your job is to find flaws, not agree.

Someone produced this analysis:
---
[PASTE SUBAGENT OUTPUT HERE]
---

Your task:
1. Check every factual claim — is it verifiable?
2. Look for logical gaps or unsupported conclusions
3. Identify what's missing or what alternative explanations exist
4. Rate confidence: HIGH (solid) / MEDIUM (mostly good, some gaps) / LOW (significant issues)

Be harsh. Your job is to catch mistakes before they cause problems.
```

## Token Cost Estimate

| Pattern | Extra Cost | Error Reduction |
|---------|-----------|-----------------|
| Research Verification | ~2x research tokens | High — catches blind spots |
| Build Verification | ~0.3x build tokens | Medium — catches bugs |
| Adversarial Verification | ~2x total tokens | Very high — full debate |

## Rule of Thumb

- **Trading/money decisions:** Always verify (Pattern 1 or 3)
- **Code with security implications:** Pattern 2
- **Simple questions or internal tools:** Skip verification, not worth the cost
- **When John says "double-check this":** Pattern 1 minimum
