# Event-Driven Trading Framework

**Source:** @0xelevenquit

**Core Idea:** Find divergences between **reality** (what an event fundamentally does) vs **positioning** (what traders think it'll do).

## Two Inputs
- **DD** (Derivatives Data) — OI, funding, liquidations
- **PA** (Price Action) — charts

## Two Event Types
1. **Anticipated** — positioning already structured around it, date known
2. **Sudden** — positioning hasn't structured yet

## Four Positioning Types
- Bullish-right (bullish positioning that turns out correct)
- Bullish-wrong (bullish positioning that turns out wrong)
- Bearish-right (bearish positioning that turns out correct)
- Bearish-wrong (bearish positioning that turns out wrong)

→ **8 trade setups** depending on combo

## Example Setups

### Anticipated Bullish-Wrong
- Long until event
- Short from there (positioning was wrong)

### Anticipated Bullish-Right
- Wait, then look for short entry after event occurs
- Close short when event-related pump retraces

### Sudden Bullish-Right
- Wait for dip that flushes overleveraged longs
- Buy at the extend of sell pressure

### Sudden Bearish-Wrong
- Wait for markets negative reaction to take place
- Buy at ideally the extend of the sell pressure

### Sudden Bearish-Right
- Short to catch violent move down
- Optionally stay short for extended period if event triggered sustained selling pressure

## Exits
Use extreme positioning (funding, OI, liquidations) as exits when your event thesis gives an entry but no clear exit.

## Derivatives Data Clues
- OI decrease/increase in anticipation of an event
- Event marking bottom/top of OI
- High funding + OI surge at bullish-right/wrong event but underwhelming price action

## Plausibility
This system is designed around the moment an event happens, the nature of that event, and the dominant positioning in the market.

Knowledge about reality is obtained by viewing an event through the lens of relevant specialist knowledge related to it. This "reality" is the nature of the event.

Positioning and reality diverge. That means the gap between positioning and reality gets bigger. But at some point, the gap between them will get smaller. This happens when positioning adapts to reality.

That's what makes the moment an event happens so relevant. Market participants are confronted with reality.
