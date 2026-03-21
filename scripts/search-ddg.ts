#!/usr/bin/env bun
/**
 * DuckDuckGo search — lite HTML endpoint (datacenter-friendly)
 * Usage: bun scripts/search-ddg.ts "query" [max_results]
 * Returns JSON: { results: [{title, url, snippet}] }
 */
const query = process.argv[2];
const maxResults = parseInt(process.argv[3] || "5", 10);

if (!query) {
  console.error('Usage: bun scripts/search-ddg.ts "query" [max_results]');
  process.exit(1);
}

const url = new URL("https://lite.duckduckgo.com/lite/");
url.searchParams.set("q", query);
url.searchParams.set("kl", "us-en");

const resp = await fetch(url.toString(), {
  headers: {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    Accept: "text/html",
  },
});

if (!resp.ok) {
  console.error(`DDG returned ${resp.status}`);
  process.exit(1);
}

const html = await resp.text();

// Extract URLs from DDG redirect links and decode them
function extractUrl(ddgHref: string): string {
  const match = ddgHref.match(/uddg=([^&]+)/);
  if (match) return decodeURIComponent(match[1]);
  return ddgHref;
}

// Parse: <a rel="nofollow" href="DDG_URL" class='result-link'>TITLE</a>
//        <td class='result-snippet'>SNIPPET</td>
const linkRe = /<a[^>]*href="([^"]*)"[^>]*class=['"]result-link['"][^>]*>(.*?)<\/a>/gs;
const snippetRe = /<td[^>]*class=['"]result-snippet['"][^>]*>([\s\S]*?)<\/td>/gs;

const links = [...html.matchAll(linkRe)];
const snippets = [...html.matchAll(snippetRe)];

const results = links.slice(0, maxResults).map((m, i) => ({
  title: m[2].replace(/<[^>]*>/g, "").replace(/&amp;/g, "&").trim(),
  url: extractUrl(m[1]),
  snippet: snippets[i]
    ? snippets[i][1].replace(/<[^>]*>/g, "").replace(/\s+/g, " ").trim()
    : "",
}));

console.log(JSON.stringify({ query, results, count: results.length }, null, 2));
