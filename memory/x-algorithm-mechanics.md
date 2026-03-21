# X/Twitter Algorithm Mechanics (2026)

Source: @phosphenq thread, Mar 2026. xAI open-sourced the algorithm Jan 2026.
GitHub: github/xai-org/x-algorithm

## Architecture (4 components)

- **Thunder** — in-memory post store. Pulls posts from followed accounts in <1ms. No DB query.
- **Phoenix** — Grok-based transformer (same arch as Grok-1). Predicts engagement probabilities per post per user.
- **Home Mixer** — orchestrator. Pulls candidates from Thunder (in-network) and Phoenix (out-of-network), filters spam/dupes, scores, selects top posts.
- **Candidate Pipeline** — framework connecting sources, hydrators, filters, scorers, selectors.

"We have eliminated every single hand-engineered feature and most heuristics from the system."

## 19 Predicted Signals

**Positive:**
- favorite, reply, repost, quote
- click, profile_click, video_view, photo_expand
- share, share_via_dm, share_via_copy_link
- dwell (how long you look at the post)
- follow_author

**Negative:**
- not_interested, block_author, mute_author, report

Plus dwell_time (continuous) and quoted_click.

All 19 weighted and summed into one score → higher score = more distribution.

## Key Mechanics

### Dwell Time
Algorithm tracks how long you look at a post. 30-second read ≠ scroll-past. Hook stops you, content holds you, dwell time = distribution signal.

### Author Diversity Decay
- 1st post in feed: full score
- 2nd: discounted
- 3rd+: exponential decay
- Cannot brute force reach by posting 20x/day. Algorithm penalizes volume.

### Out-of-Network Penalty
- Non-followers: score multiplied by factor < 1.0
- Only way to beat: trigger such strong engagement predictions that discounted score still beats in-network content

## Virality Scoring Formula

Normalized by author follower count:

```
raw = (quotes × 8.0) + (bookmarks × 6.0) + (replies × 4.0) + (retweets × 3.0) + (likes × 1.0)
score = (raw / follower_count) × 1000
```

Thresholds:
- > 50: outperforming baseline
- > 100: viral for this account size
- > 200: exceptional

## Dominant Formats (Mar 2026)

### Movie Clip Quote Tweets
- Someone publishes article/announcement
- Second account quote-tweets with 10-20s emotional reaction clip
- 1-3 lines provocative text
- Triggers: video_view, dwell_time, reply, quote, click, share_via_dm, follow_author
- One format, 5+ positive signals firing simultaneously

### Other high-performers:
- **CAPS LOCK news drops** ("JUST IN:" / "BREAKING:") — first to post captures all engagement
- **Specific numbers + technical framing** — "100+ signals. 38 indicators. 80% win rate." Triggers bookmarks.
- **Threads with standalone first tweets** — threads get 2.4x more engagement than single tweets. Best ones: first tweet works as standalone viral post, depth in 8-12 tweets below.

## X API Pricing (Feb 2026)

Pay-per-use model:
- Posts Read: $0.005/post
- User Read: $0.010/user
- Content Create: $0.010/request

No subscription. Buy credits, deduct as you go.
500 posts = $2.50

**Limitation:** Official API only gives Following tab (reverse chronological). NOT the For You algorithmic feed.

**Free alternative:** Twikit (scrapes internal GraphQL endpoints) — gives For You access but violates ToS.

---

## Self-Learning Agent Pattern (transferable to trading)

Feedback loop:
1. Agent recommends → you publish
2. Agent tracks performance (pulls actual metrics)
3. Agent compares recommendation vs results
4. Agent learns what YOUR audience responds to
5. Agent adjusts future recommendations

**For trading adaptation:**
- Agent recommends trade → you execute
- Agent tracks PnL, win rate, drawdown
- Agent compares signal vs outcome
- Agent learns what market conditions favor which strategies
- Agent adjusts position sizing, entry timing, market selection

### Daily workflow:
1. Read feed (or market data) → score everything
2. Analyze patterns (what's working now)
3. Learn from past performance (what worked for YOU)
4. Generate today's plan (specific recommendations with reasoning)

### Cost reference (for content agent):
- X API: ~$2.50/day
- Claude API: ~$0.40/day
- VPS: $5/month
- Total: ~$17-92/month depending on API choice
