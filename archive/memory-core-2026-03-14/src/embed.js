// src/embed.js
import { env } from '@xenova/transformers';
env.allowLocalModels = false;
env.allowRemoteModels = true;

import { pipeline } from '@xenova/transformers';

let featureExtractor = null;

/**
 * Initialize the embedding model.
 * @returns {Promise<void>}
 */
export async function init() {
  if (featureExtractor) return;
  featureExtractor = await pipeline('feature-extraction', 'Xenova/all-MiniLM-L6-v2');
}

/**
 * Generate embedding for a single text.
 * @param {string} text - Input text
 * @returns {Promise<Float32Array>} - 384-dimensional embedding
 */
export async function embed(text) {
  if (!featureExtractor) await init();
  const output = await featureExtractor(text, { pooling: 'mean', normalize: true, toType: 'float32' });
  // output is a Tensor with dims [1, 384], data is Float32Array
  return new Float32Array(output.data);
}

/**
 * Generate embeddings for multiple texts.
 * @param {string[]} texts - Array of input texts
 * @returns {Promise<Float32Array[]>} - Array of 384-dimensional embeddings
 */
export async function embedBatch(texts) {
  if (!featureExtractor) await init();
  const output = await featureExtractor(texts, { pooling: 'mean', normalize: true, toType: 'float32' });
  // output dims: [batch, 384] — need to split into per-text arrays
  const batchSize = texts.length;
  const dim = 384;
  const results = [];
  for (let i = 0; i < batchSize; i++) {
    results.push(new Float32Array(output.data.slice(i * dim, (i + 1) * dim)));
  }
  return results;
}

/**
 * Compute cosine similarity between two Float32Arrays.
 * @param {Float32Array} a - First vector
 * @param {Float32Array} b - Second vector
 * @returns {number} - Cosine similarity in range [-1, 1]
 */
export function cosineSimilarity(a, b) {
  if (a.length !== b.length) {
    throw new Error('Vectors must be same length');
  }
  let dot = 0, normA = 0, normB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  return dot / (Math.sqrt(normA) * Math.sqrt(normB));
}
