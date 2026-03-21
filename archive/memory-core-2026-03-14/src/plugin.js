// src/plugin.js
import { init as initEmbed } from './embed.js';
import store from './store.js';
import { recall } from './recall.js';
import { captureTurn } from './capture.js';

let initialized = false;

async function ensureInit() {
  if (!initialized) {
    await initEmbed();
    store.init();
    initialized = true;
  }
}

function debug(...args) {
  if (process.env.MEMORY_CORE_DEBUG) {
    console.log('[memory-core]', ...args);
  }
}

/**
 * OpenClaw plugin for memory-core integration.
 * 
 * Hooks:
 * - beforeAgentStart: Auto-recall relevant memories for the incoming message
 * - agentEnd: Auto-capture facts from the conversation turn
 */
export const memoryCorePlugin = {
  name: 'memory-core',
  version: '0.1.0',

  /**
   * Before agent processes a message — inject relevant memories.
   * @param {Object} context
   * @param {string} context.message - The incoming user message
   * @param {string} context.sessionId - Current session ID
   * @param {string} [context.agentId] - Agent identifier
   * @returns {Promise<{inject: string}|null>} - Text to inject into agent context, or null
   */
  async beforeAgentStart(context) {
    try {
      await ensureInit();
      
      const { message, sessionId, agentId } = context;
      
      // Skip recall for very short or command-like messages
      if (!message || message.length < 10) {
        debug('Skipping recall: message too short');
        return null;
      }

      const result = await recall({
        message,
        topK: 5,
        maxDistance: 0.35,
        agentId: agentId || 'main',
      });

      if (result.count === 0) {
        debug('No relevant memories found');
        return null;
      }

      debug(`Recalled ${result.count} memories`);
      return { inject: result.formatted };
    } catch (err) {
      // Don't crash the gateway if memory fails
      debug('beforeAgentStart error:', err.message);
      return null;
    }
  },

  /**
   * After agent responds — capture new facts.
   * @param {Object} context
   * @param {string} context.userMessage - The user's message
   * @param {string} context.assistantMessage - The agent's response
   * @param {string} context.sessionId - Current session ID
   * @param {string} [context.agentId] - Agent identifier
   * @returns {Promise<void>}
   */
  async agentEnd(context) {
    try {
      await ensureInit();
      
      const { userMessage, assistantMessage, sessionId, agentId } = context;
      
      const result = await captureTurn({
        userMessage,
        assistantMessage,
        sessionId: sessionId || 'unknown',
        agentId: agentId || 'main',
      });

      if (result.stored > 0) {
        debug(`Captured ${result.stored} facts:`, result.facts);
      }
    } catch (err) {
      // Don't crash the gateway if memory fails
      debug('agentEnd error:', err.message);
    }
  },
};
