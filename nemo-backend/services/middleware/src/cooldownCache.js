/**
 * CooldownCache - In-memory TTL cache for warning suppression
 * 
 * Avoids repeated warnings for the same place/complaint within 30 minutes.
 * Property 19: Cooldown Cache Round-Trip
 * Property 20: Suppressed Warning Skips Response
 */

const COOLDOWN_MS = 30 * 60 * 1000; // 30 minutes

// Map<string, number> — id → timestamp ms
const cache = new Map();

/**
 * Check if an ID is currently suppressed (warned within cooldown period).
 * 
 * @param {string} id - Place ID or complaint ID
 * @returns {boolean} True if suppressed, false if can warn
 */
export function isSuppressed(id) {
  const last = cache.get(id);
  return last !== undefined && (Date.now() - last) < COOLDOWN_MS;
}

/**
 * Record a warning for an ID, starting the cooldown period.
 * 
 * @param {string} id - Place ID or complaint ID
 */
export function record(id) {
  cache.set(id, Date.now());
}

/**
 * Clear a specific ID from the cache (for testing).
 * 
 * @param {string} id - Place ID or complaint ID
 */
export function clear(id) {
  cache.delete(id);
}

/**
 * Clear all entries from the cache (for testing).
 */
export function clearAll() {
  cache.clear();
}

/**
 * Get the current size of the cache.
 * 
 * @returns {number} Number of entries in cache
 */
export function size() {
  return cache.size;
}

/**
 * Get the cooldown duration in milliseconds.
 * 
 * @returns {number} Cooldown duration
 */
export function getCooldownMs() {
  return COOLDOWN_MS;
}

export default {
  isSuppressed,
  record,
  clear,
  clearAll,
  size,
  getCooldownMs
};
