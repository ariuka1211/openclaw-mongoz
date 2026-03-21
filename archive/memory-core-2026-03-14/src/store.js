// src/store.js
import Database from 'better-sqlite3';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { mkdir } from 'fs/promises';

const __filename = fileURLToPath(import.meta.url);
const __dirname = join(dirname(__filename), '..');

class VectorStore {
  constructor() {
    this.db = null;
  }

  async init() {
    const dataDir = join(__dirname, 'data');
    await mkdir(dataDir, { recursive: true });
    
    this.db = new Database(join(dataDir, 'memory.db'));
    this.db.pragma('journal_mode = WAL');
    
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS chunks (
        id TEXT PRIMARY KEY,
        file_path TEXT NOT NULL,
        chunk_index INTEGER,
        content TEXT NOT NULL,
        header TEXT,
        embedding BLOB NOT NULL,
        topic TEXT,
        scope TEXT DEFAULT 'long-term',
        agent_id TEXT DEFAULT 'main',
        user_id TEXT DEFAULT 'default',
        source_hash TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        metadata TEXT
      );
      
      CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_path);
      CREATE INDEX IF NOT EXISTS idx_chunks_topic ON chunks(topic);
      CREATE INDEX IF NOT EXISTS idx_chunks_agent ON chunks(agent_id, user_id);
      CREATE INDEX IF NOT EXISTS idx_chunks_scope ON chunks(scope);
      
      CREATE TABLE IF NOT EXISTS file_sync (
        file_path TEXT PRIMARY KEY,
        last_hash TEXT NOT NULL,
        last_synced TEXT DEFAULT (datetime('now')),
        chunk_count INTEGER
      );
    `);
    
    // Create indexes
    this.db.exec(`
      CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_path);
    `);
    this.db.exec(`
      CREATE INDEX IF NOT EXISTS idx_chunks_topic ON chunks(topic);
    `);
    this.db.exec(`
      CREATE INDEX IF NOT EXISTS idx_chunks_agent ON chunks(agent_id, user_id);
    `);
    this.db.exec(`
      CREATE INDEX IF NOT EXISTS idx_chunks_scope ON chunks(scope);
    `);
  }

  upsert(chunk) {
    this.db.prepare(`
      INSERT OR REPLACE INTO chunks (
        id, file_path, chunk_index, content, header, embedding, topic, scope, 
        agent_id, user_id, source_hash, created_at, updated_at, metadata
      ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
      )
    `).run(
      chunk.id,
      chunk.file_path,
      chunk.chunk_index,
      chunk.content,
      chunk.header,
      Buffer.from(chunk.embedding.buffer, chunk.embedding.byteOffset, chunk.embedding.byteLength),
      chunk.topic,
      chunk.scope,
      chunk.agent_id,
      chunk.user_id,
      chunk.source_hash,
      chunk.created_at,
      chunk.updated_at,
      chunk.metadata ? JSON.stringify(chunk.metadata) : null
    );
  }

  search(queryEmbedding, options = {}) {
    const {
      topK = 10,
      maxDistance = 0.7,
      topic,
      scope,
      agentId,
      userId
    } = options;
    
    let sql = `
      SELECT id, file_path, chunk_index, content, header, embedding, topic, scope, 
             agent_id, user_id, source_hash, created_at, updated_at, metadata
      FROM chunks
    `;
    const params = [];
    
    const whereConditions = [];
    if (topic) {
      whereConditions.push('topic = ?');
      params.push(topic);
    }
    if (scope) {
      whereConditions.push('scope = ?');
      params.push(scope);
    }
    if (agentId) {
      whereConditions.push('agent_id = ?');
      params.push(agentId);
    }
    if (userId) {
      whereConditions.push('user_id = ?');
      params.push(userId);
    }
    
    if (whereConditions.length > 0) {
      sql += ' WHERE ' + whereConditions.join(' AND ');
    }
    
    const stmt = this.db.prepare(sql);
    const rows = stmt.all(...params);
    
    return rows
      .map(row => {
        const embedding = new Float32Array(row.embedding.buffer, row.embedding.byteOffset, row.embedding.length / 4);
        const similarity = this.cosineSimilarity(queryEmbedding, embedding);
        const distance = 1 - similarity;
        
        return {
          ...row,
          embedding: undefined,
          similarity,
          distance
        };
      })
      .filter(result => result.distance <= maxDistance)
      .sort((a, b) => a.distance - b.distance)
      .slice(0, topK);
  }

  deleteByFile(filePath) {
    this.db.prepare('DELETE FROM chunks WHERE file_path = ?').run(filePath);
    this.db.prepare('DELETE FROM file_sync WHERE file_path = ?').run(filePath);
  }

  getStats() {
    const totalChunks = this.db.prepare('SELECT COUNT(*) as count FROM chunks').get().count;
    
    const byTopic = this.db.prepare(`
      SELECT topic, COUNT(*) as count 
      FROM chunks 
      WHERE topic IS NOT NULL 
      GROUP BY topic
    `).all();
    
    const byFile = this.db.prepare(`
      SELECT file_path, COUNT(*) as count 
      FROM chunks 
      GROUP BY file_path
    `).all();
    
    const byAgent = this.db.prepare(`
      SELECT agent_id, user_id, COUNT(*) as count 
      FROM chunks 
      GROUP BY agent_id, user_id
    `).all();
    
    return {
      totalChunks,
      byTopic,
      byFile,
      byAgent
    };
  }

  getSyncState(filePath) {
    return this.db.prepare('SELECT * FROM file_sync WHERE file_path = ?').get(filePath);
  }

  setSyncState(filePath, hash, chunkCount) {
    this.db.prepare(`
      INSERT OR REPLACE INTO file_sync (file_path, last_hash, last_synced, chunk_count)
      VALUES (?, ?, datetime('now'), ?)
    `).run(filePath, hash, chunkCount);
  }

  cosineSimilarity(a, b) {
    let dot = 0;
    let normA = 0;
    let normB = 0;
    for (let i = 0; i < a.length; i++) {
      dot += a[i] * b[i];
      normA += a[i] * a[i];
      normB += b[i] * b[i];
    }
    return dot / (Math.sqrt(normA) * Math.sqrt(normB));
  }

  close() {
    if (this.db) {
      this.db.close();
      this.db = null;
    }
  }
}

export { VectorStore };
export default new VectorStore();