// src/watcher.js
import { watch } from 'chokidar';
import { readFile, access, readdir } from 'fs/promises';
import { join, relative, dirname } from 'path';
import { createHash } from 'crypto';
import { chunkMarkdown } from './chunker.js';
import { embed, init as initEmbed } from './embed.js';
import store from './store.js';

const WORKSPACE_ROOT = process.env.WORKSPACE_ROOT || '/root/.openclaw/workspace';

const TRACKED_FILES = [
  'MEMORY.md',
  'learning.md',
  'TOOLS.md',
  'SESSION_STATE.md',
];

const TRACKED_DIRS = [
  'memory',    // memory/*.md
  'daily',     // daily/*.md
];

let watcher = null;

/**
 * Check if a file exists.
 */
async function fileExists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

/**
 * Process a single file: chunk, embed, upsert.
 */
async function processFile(filePath) {
  const relativePath = relative(WORKSPACE_ROOT, filePath);
  const content = await readFile(filePath, 'utf-8');
  const chunks = chunkMarkdown(relativePath, content);

  let updated = 0;
  for (const chunk of chunks) {
    // Check if chunk changed
    const existing = store.getSyncState(relativePath);
    if (existing && existing.last_hash === chunk.source_hash) {
      continue; // Skip unchanged
    }

    // Embed and store
    const embedding = await embed(chunk.content);
    store.upsert({
      ...chunk,
      embedding,
      topic: null, // Will be classified later
    });
    updated++;
  }

  return { chunks: chunks.length, updated };
}

/**
 * Remove all chunks for a deleted file.
 */
function removeFile(filePath) {
  const relativePath = relative(WORKSPACE_ROOT, filePath);
  store.deleteByFile(relativePath);
}

/**
 * Find all markdown files in a directory.
 */
async function findMdFiles(dir) {
  const files = [];
  try {
    const entries = await readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isFile() && entry.name.endsWith('.md')) {
        files.push(join(dir, entry.name));
      }
    }
  } catch {
    // Directory doesn't exist
  }
  return files;
}

/**
 * Run initial sync of all tracked files.
 * @returns {Promise<{files: number, chunks: number, updated: number}>}
 */
export async function initialSync() {
  await initEmbed();
  store.init();

  let files = 0;
  let totalChunks = 0;
  let totalUpdated = 0;

  // Collect all tracked files
  const allFiles = [];

  // Single files
  for (const file of TRACKED_FILES) {
    const filePath = join(WORKSPACE_ROOT, file);
    if (await fileExists(filePath)) {
      allFiles.push(filePath);
    }
  }

  // Directory globs (*.md in tracked dirs)
  for (const dir of TRACKED_DIRS) {
    const dirPath = join(WORKSPACE_ROOT, dir);
    const mdFiles = await findMdFiles(dirPath);
    allFiles.push(...mdFiles);
  }

  // Process each file
  for (const filePath of allFiles) {
    const relPath = relative(WORKSPACE_ROOT, filePath);
    try {
      const content = await readFile(filePath, 'utf-8');
      const chunks = chunkMarkdown(relPath, content);

      let updated = 0;
      for (const chunk of chunks) {
        const embedding = await embed(chunk.content);
        const topic = (await import('./topics.js')).classifyTopic(chunk.content, chunk.header);
        store.upsert({
          ...chunk,
          embedding,
          topic,
        });
        updated++;
      }

      files++;
      totalChunks += chunks.length;
      totalUpdated += updated;

      // Update sync state
      const hash = createHash('sha256').update(content).digest('hex');
      store.setSyncState(relPath, hash, chunks.length);
    } catch (err) {
      console.warn(`Warning: Failed to sync ${relPath}: ${err.message}`);
    }
  }

  return { files, chunks: totalChunks, updated: totalUpdated };
}

/**
 * Start watching files for changes.
 * @param {Function} onChange - Callback for changes: ({event, filePath, chunks?}) => void
 */
export async function startWatching(onChange) {
  await initEmbed();
  store.init();

  const patterns = [
    ...TRACKED_FILES.map(f => join(WORKSPACE_ROOT, f)),
    ...TRACKED_DIRS.map(d => join(WORKSPACE_ROOT, d, '*.md')),
  ];

  watcher = watch(patterns, {
    ignoreInitial: true,
    awaitWriteFinish: {
      stabilityThreshold: 1000,
      pollInterval: 100,
    },
    persistent: true,
    ignored: [
      '**/node_modules/**',
      '**/data/**',
      '**/.git/**',
    ],
  });

  watcher
    .on('add', async (filePath) => {
      try {
        const result = await processFile(filePath);
        onChange({ event: 'add', filePath, chunks: result.chunks });
      } catch (err) {
        console.warn(`Warning: Failed to process added file ${filePath}: ${err.message}`);
      }
    })
    .on('change', async (filePath) => {
      try {
        const result = await processFile(filePath);
        onChange({ event: 'change', filePath, chunks: result.chunks });
      } catch (err) {
        console.warn(`Warning: Failed to process changed file ${filePath}: ${err.message}`);
      }
    })
    .on('unlink', (filePath) => {
      try {
        removeFile(filePath);
        onChange({ event: 'unlink', filePath });
      } catch (err) {
        console.warn(`Warning: Failed to remove file ${filePath}: ${err.message}`);
      }
    });

  console.log(`Watching ${patterns.length} file patterns for changes...`);
}

/**
 * Stop watching files.
 */
export async function stopWatching() {
  if (watcher) {
    await watcher.close();
    watcher = null;
    console.log('File watcher stopped.');
  }
}
