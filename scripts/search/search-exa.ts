#!/usr/bin/env bun
/**
 * Exa search — AI-powered semantic search
 * Usage: EXA_API_KEY="${EXA_API_KEY:-}"
 * Returns JSON: { results: [{title, url, snippet, score}] }
 *
 * Free tier: 1,000 requests/month at exa.ai
 * Set EXA_API_KEY in env or OpenClaw config
 */
const query = process.argv[2];
const maxResults = parseInt(process.argv[3] || "5", 10);
const apiKey = process.env.EXA_API_KEY || "<REDACTED>";

if (!query) {
  console.error('Usage: EXA_API_KEY="${EXA_API_KEY:-}"');
  process.exit(1);
}
if (!apiKey) {
  console.error("EXA_API_KEY not set. Get free key at https://exa.ai");
  process.exit(1);
}

const resp = await fetch("https://api.exa.ai/search", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "x-api-key": apiKey,
  },
  body: JSON.stringify({
    query,
    numResults: maxResults,
    type: "auto",
    contents: {
      text: { maxCharacters: 500 },
    },
  }),
});

if (!resp.ok) {
  const err = await resp.text();
  console.error(`Exa API error ${resp.status}: ${err}`);
  process.exit(1);
}

const data = await resp.json();
const results = (data.results || []).map((r: any) => ({
  title: r.title,
  url: r.url,
  snippet: r.text?.slice(0, 300) || r.highlight || "",
  score: r.score,
  published: r.publishedDate || null,
}));

console.log(JSON.stringify({ query, results, count: results.length, requestId: data.requestId }, null, 2));
