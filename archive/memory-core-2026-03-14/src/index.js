// src/index.js
export { init as initEmbed, embed, embedBatch, cosineSimilarity } from './embed.js';
export { VectorStore } from './store.js';
export { chunkMarkdown } from './chunker.js';
export { startWatching, stopWatching, initialSync } from './watcher.js';
export { runSync } from './sync.js';
export { captureTurn } from './capture.js';
export { recall } from './recall.js';
export { classifyTopic } from './topics.js';
export { memoryCorePlugin } from './plugin.js';
