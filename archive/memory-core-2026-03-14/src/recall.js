// src/recall.js
import { embed, init as initEmbed } from './embed.js';
import store from './store.js';

// Max tokens for formatted output (rough estimate: 1 token ≈ 4 chars)
const MAX_CHARS = 2000;

/**
 * Format a date to short form (e.g., "Mar 13").
 */
function formatDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${months[d.getMonth()]} ${d.getDate()}`;
}

/**
 * Recall relevant memories for a given message.
 * @param {Object} params
 * @param {string} params.message - The message to find relevant memories for
 * @param {number} [params.topK=10] - Maximum results to return
 * @param {number} [params.maxDistance=0.3] - Maximum cosine distance (lower = more similar)
 * @param {string} [params.topic] - Filter by topic
 * @param {string} [params.scope] - Filter by scope
 * @param {string} [params.agentId] - Filter by agent
 * @param {string} [params.userId] - Filter by user
 * @returns {Promise<{memories: object[], formatted: string, count: number}>}
 */
export async function recall({ message, topK = 10, maxDistance = 0.7, topic, scope, agentId, userId }) {
  await initEmbed();
  await store.init();

  const queryEmbedding = await embed(message);
  
  const results = store.search(queryEmbedding, {
    topK,
    maxDistance,
    topic,
    scope,
    agentId,
    userId,
  });

  if (results.length === 0) {
    return { memories: [], formatted: '', count: 0 };
  }

  // Group by topic
  const byTopic = {};
  for (const r of results) {
    const t = r.topic || 'general';
    if (!byTopic[t]) byTopic[t] = [];
    byTopic[t].push(r);
  }

  // Format output
  const lines = ['[Semantic Memory]'];
  let totalChars = lines.join('\n').length;

  for (const [topicName, items] of Object.entries(byTopic)) {
    for (const item of items) {
      const header = item.header ? ` (${item.header.replace(/^#+\s*/, '')})` : '';
      const date = formatDate(item.created_at);
      const content = item.content.length > 150 
        ? item.content.slice(0, 150) + '...' 
        : item.content;
      
      const line = `• [${topicName}] ${content}${date ? ` (${date})` : ''}`;
      
      totalChars += line.length + 1;
      if (totalChars > MAX_CHARS) {
        lines.push('...');
        break;
      }
      
      lines.push(line);
    }
    
    if (totalChars > MAX_CHARS) break;
  }

  lines.push('[/Semantic Memory]');

  return {
    memories: results,
    formatted: lines.join('\n'),
    count: results.length,
  };
}
