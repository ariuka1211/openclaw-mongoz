// src/sync.js
import { init as initEmbed } from './embed.js';
import { initialSync as watcherInitialSync } from './watcher.js';

/**
 * Run a full sync of all memory files to the vector store.
 * @returns {Promise<{files: number, chunks: number, updated: number}>}
 */
export async function runSync() {
  console.log('Starting memory sync...');
  
  const result = await watcherInitialSync();
  
  console.log(`Synced ${result.files} files, ${result.chunks} chunks (${result.updated} new/updated)`);
  
  return result;
}
