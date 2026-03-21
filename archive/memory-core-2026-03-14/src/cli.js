// src/cli.js
import { init as initEmbed, embed, cosineSimilarity } from './embed.js';
import store from './store.js';
import { runSync } from './sync.js';
import { startWatching, stopWatching } from './watcher.js';
import { captureTurn } from './capture.js';
import { recall } from './recall.js';

async function init() {
  await initEmbed();
  await store.init();
}

// --- Commands ---

async function stats() {
  await init();
  const stats = store.getStats();
  console.log('Memory Core Statistics:');
  console.log(`  Total chunks: ${stats.totalChunks}`);
  console.log(`  By topic:`);
  if (stats.byTopic.length === 0) {
    console.log(`    (no topics yet)`);
  } else {
    for (const item of stats.byTopic) {
      console.log(`    ${item.topic || '(null)'}: ${item.count}`);
    }
  }
  console.log(`  By file:`);
  if (stats.byFile.length === 0) {
    console.log(`    (no files yet)`);
  } else {
    for (const item of stats.byFile) {
      console.log(`    ${item.file_path}: ${item.count}`);
    }
  }
}

async function search(query) {
  await init();
  const queryEmbedding = await embed(query);
  const results = store.search(queryEmbedding, { topK: 5, maxDistance: 0.7 });

  console.log(`Search results for: "${query}"`);
  console.log(`Found ${results.length} results:\n`);

  if (results.length === 0) {
    console.log('  (no results found)');
    return;
  }

  for (let i = 0; i < results.length; i++) {
    const r = results[i];
    console.log(`  ${i + 1}. [${r.file_path}] ${(r.similarity * 100).toFixed(1)}% similar`);
    console.log(`     ${r.content.slice(0, 120)}${r.content.length > 120 ? '...' : ''}`);
    console.log();
  }
}

async function sync() {
  await runSync();
  process.exit(0);
}

async function watch() {
  console.log('Starting file watcher...');
  await startWatching(({ event, filePath, chunks }) => {
    console.log(`[${event}] ${filePath}${chunks ? ` (${chunks} chunks)` : ''}`);
  });

  // Keep running
  process.on('SIGINT', async () => {
    await stopWatching();
    process.exit(0);
  });
  process.on('SIGTERM', async () => {
    await stopWatching();
    process.exit(0);
  });
}

async function captureCmd(userMsg, assistantMsg) {
  if (!userMsg) {
    console.error('Usage: node src/cli.js capture "<user message>" "<assistant response>"');
    process.exit(1);
  }
  const result = await captureTurn({
    userMessage: userMsg,
    assistantMessage: assistantMsg || '',
    sessionId: 'cli-test',
  });
  console.log(`Captured ${result.stored} facts:`);
  for (const fact of result.facts) {
    console.log(`  • ${fact}`);
  }
}

async function recallCmd(query) {
  if (!query) {
    console.error('Usage: node src/cli.js recall "<query>"');
    process.exit(1);
  }
  const result = await recall({ message: query, topK: 5 });
  console.log(`Found ${result.count} relevant memories:\n`);
  console.log(result.formatted);
}

// --- Main ---

const command = process.argv[2];
const arg1 = process.argv[3];
const arg2 = process.argv[4];

try {
  switch (command) {
    case 'stats':
      await stats();
      break;
    case 'search':
      await search(arg1);
      break;
    case 'sync':
      await sync();
      break;
    case 'watch':
      await watch();
      break;
    case 'capture':
      await captureCmd(arg1, arg2);
      break;
    case 'recall':
      await recallCmd(arg1);
      break;
    default:
      console.log(`
Memory Core CLI

Commands:
  stats                        Show vector store statistics
  search "<query>"             Search memories by semantic similarity
  sync                         Sync all memory files to vector store
  watch                        Watch files for changes (daemon)
  capture "<msg>" "<response>" Test auto-capture
  recall "<query>"             Test auto-recall

Examples:
  node src/cli.js stats
  node src/cli.js search "model monitor incident"
  node src/cli.js sync
  node src/cli.js recall "John's preferences"
`);
      process.exit(1);
  }
} catch (err) {
  console.error(`Error: ${err.message}`);
  process.exit(1);
}
