// src/capture.js
import { embed, init as initEmbed } from './embed.js';
import store from './store.js';
import { classifyTopic } from './topics.js';
import { createHash } from 'crypto';

// Noise patterns — messages to skip entirely
const NOISE_PATTERNS = [
  /^(ok|thanks|ty|done|sure|yep|nope|right|continue|go ahead|nice|cool|lol|haha|okay|alright|got it|perfect|great|awesome|sweet|yup|nah|no|yes|yeah|k|kk|10-4|roger|copy|affirmative|sounds good|makes sense|understood|noted|👍|👎|✅|❌|🔥|💯|🎉|🤔|😅|😂)$/i,
  /^what\??$/i,
  /^how\??$/i,
  /^why\??$/i,
  /^when\??$/i,
  /^where\??$/i,
  /^who\??$/i,
  /^(search|find|look|read|open|show|list|check)\s+(for|up|at|me|the|in|on)\b/i,
  /^\s*$/,  // empty or whitespace only
];

// Fact extraction patterns — things worth remembering
const FACT_PATTERNS = [
  // Preferences and opinions
  { pattern: /\b(i prefer|i like|i hate|i love|i use|i want|i need|i wish|i'd like|i would like|my preference is|my approach is)\b/i, topic: 'preferences' },
  
  // Decisions
  { pattern: /\b(let's use|we decided|going with|i'll go with|we're using|we should use|chosen|picked|selected|decided on)\b/i, topic: 'technical_decisions' },
  
  // Identity and personal info
  { pattern: /\b(my name is|i'm|i am|call me|i live|i'm located|i'm based|my timezone is|pronouns)\b/i, topic: 'personal_info' },
  
  // Configurations and setups
  { pattern: /\b(server|srv|host|ip|port|config|setting|env|environment|deploy|install|setup|database|db|api key|token|secret|credential)\b.*\b(is|are|set to|equals|=|:)\b/i, topic: 'project_context' },
  
  // Key context worth remembering
  { pattern: /\b(remember that|note that|important|key thing|the trick is|the key is|heads up|fyi|for your information|just so you know)\b/i, topic: 'general' },
  
  // Action items
  { pattern: /\b(todo|task|need to|should|must|don't forget|remember to|make sure|follow up|deadline|by tomorrow|next week)\b/i, topic: 'action_items' },
  
  // Technical decisions
  { pattern: /\b(chose|chosen|picking|using.*because|decided to|switched to|moved to|replaced.*with|instead of|better than|more reliable than)\b/i, topic: 'technical_decisions' },
];

// Minimum message length to consider
const MIN_LENGTH = 15;

/**
 * Check if a message is noise (not worth capturing).
 */
function isNoise(message) {
  if (!message || message.length < MIN_LENGTH) return true;
  return NOISE_PATTERNS.some(p => p.test(message.trim()));
}

/**
 * Extract facts from a user message.
 * @param {string} message - User message
 * @returns {{text: string, topic: string}[]}
 */
function extractFacts(message) {
  const facts = [];
  const sentences = message.split(/[.!?\n]+/).filter(s => s.trim().length >= MIN_LENGTH);

  for (const sentence of sentences) {
    for (const { pattern, topic } of FACT_PATTERNS) {
      if (pattern.test(sentence)) {
        facts.push({
          text: sentence.trim(),
          topic,
        });
        break; // One topic per sentence
      }
    }
  }

  return facts;
}

/**
 * Capture a conversation turn and store extractable facts.
 * @param {Object} params
 * @param {string} params.userMessage - The user's message
 * @param {string} params.assistantMessage - The assistant's response
 * @param {string} params.sessionId - Current session ID
 * @param {string} [params.agentId='main'] - Agent identifier
 * @param {string} [params.userId='default'] - User identifier
 * @returns {Promise<{stored: number, facts: string[]}>}
 */
export async function captureTurn({ userMessage, assistantMessage, sessionId, agentId = 'main', userId = 'default' }) {
  if (isNoise(userMessage)) {
    return { stored: 0, facts: [] };
  }

  const extractedFacts = extractFacts(userMessage);
  if (extractedFacts.length === 0) {
    return { stored: 0, facts: [] };
  }

  await initEmbed();
  store.init();

  const stored = [];
  for (const { text, topic } of extractedFacts) {
    const embedding = await embed(text);
    const id = createHash('sha256')
      .update(`capture:${sessionId}:${text}`)
      .digest('hex');

    store.upsert({
      id,
      file_path: 'auto-capture',
      chunk_index: stored.length,
      content: text,
      header: null,
      embedding,
      topic,
      scope: 'session',
      agent_id: agentId,
      user_id: userId,
      source_hash: createHash('sha256').update(text).digest('hex'),
      metadata: { sessionId, source: 'auto-capture' },
    });

    stored.push(text);
  }

  return { stored: stored.length, facts: stored };
}
