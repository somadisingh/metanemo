/**
 * Proactive Alert Engine
 *
 * Fires every 2 minutes per connected client.
 * Fetches situation report for the client's current GPS location,
 * filters through cooldown cache, formats as bullet points,
 * and sends a proactiveAlert message over WebSocket.
 *
 * Message format sent to iOS app:
 * {
 *   "serverContent": {
 *     "proactiveAlert": {
 *       "bullets": ["E/F trains delayed", "Broken sidewalk 200m away", ...],
 *       "safetyScore": 75,
 *       "safetyLevel": "fair"
 *     }
 *   }
 * }
 */

import logger from './logger.js';
import { getSituationReport } from './nemoClawClient.js';

const ALERT_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes // 2 minutes

// Map<clientId, intervalId>
const alertTimers = new Map();

/**
 * Format situation report into bullet point strings.
 * Only includes HIGH and MEDIUM severity items not in cooldown.
 *
 * @param {Object} report - Situation report from NemoClaw
 * @returns {{ bullets: string[] }}
 */
function formatBullets(report) {
  const bullets = [];

  if (!report) return { bullets };

  // MTA alerts
  const topHazards = report.top_hazards || [];
  for (const h of topHazards) {
    const dist = h.distance_meters ? ` (${Math.round(h.distance_meters)}m)` : '';
    const desc = (h.description || h.type || 'Alert').slice(0, 80);
    bullets.push(`${desc}${dist}`);
  }

  // Edge case alerts
  const alerts = report.alerts || [];
  for (const a of alerts) {
    if (a.severity === 'high' || a.severity === 'medium') {
      bullets.push(a.message?.slice(0, 80) || a.type);
    }
  }

  // Collision hotspots summary
  const collisionCount = report.summary?.collision_hotspots || 0;
  if (collisionCount > 0) {
    bullets.push(`${collisionCount} pedestrian collision hotspot${collisionCount > 1 ? 's' : ''} nearby`);
  }
  return { bullets };
}

/**
 * Run one proactive alert cycle for a client.
 *
 * @param {WebSocket} ws - Client WebSocket
 * @param {string} clientId
 * @param {{ latitude: number, longitude: number }} state - Client state
 */
async function runAlertCycle(ws, clientId, state) {
  if (ws.readyState !== ws.OPEN) return;

  const { latitude, longitude } = state;

  // Skip if still at default coords (client hasn't sent GPS yet)
  if (latitude === 40.7128 && longitude === -74.0060 && !state.hasReceivedGPS) {
    logger.debug({ msg: 'Proactive alert skipped — no GPS received yet', clientId });
    return;
  }

  logger.debug({ msg: 'Running proactive alert cycle', clientId, latitude, longitude });

  try {
    const report = await getSituationReport(latitude, longitude);
    if (!report) return;

    const { bullets } = formatBullets(report);

    logger.debug({
      msg: 'Proactive alert cycle complete',
      clientId,
      bulletCount: bullets.length,
      safetyScore: report.safety_score
    });

    // Only send if there's something to report
    if (bullets.length === 0) return;

    ws.send(JSON.stringify({
      serverContent: {
        proactiveAlert: {
          bullets,
          safetyScore: report.safety_score,
          safetyLevel: report.safety_level,
          nearbyTransit: report.nearby_transit?.routes || []
        }
      }
    }));

  } catch (err) {
    logger.error({ msg: 'Proactive alert cycle error', clientId, error: err.message });
  }
}

/**
 * Start the proactive alert timer for a connected client.
 *
 * @param {WebSocket} ws
 * @param {string} clientId
 * @param {Function} getState - Function that returns current client state
 */
export function startProactiveAlerts(ws, clientId, getState) {
  // Clear any existing timer for this client
  stopProactiveAlerts(clientId);

  logger.info({ msg: 'Starting proactive alerts', clientId, intervalMs: ALERT_INTERVAL_MS });

  // Fire first alert after 15s (let GPS arrive), then every 5 minutes
  const initialDelay = setTimeout(async () => {
    const state = getState(clientId);
    if (state) await runAlertCycle(ws, clientId, state);
  }, 15000);

  const timer = setInterval(async () => {
    const state = getState(clientId);
    if (!state) {
      stopProactiveAlerts(clientId);
      return;
    }
    await runAlertCycle(ws, clientId, state);
  }, ALERT_INTERVAL_MS);

  alertTimers.set(clientId, { timer, initialDelay });
}

/**
 * Stop the proactive alert timer for a client.
 *
 * @param {string} clientId
 */
export function stopProactiveAlerts(clientId) {
  const entry = alertTimers.get(clientId);
  if (entry) {
    clearInterval(entry.timer || entry);
    if (entry.initialDelay) clearTimeout(entry.initialDelay);
    alertTimers.delete(clientId);
    logger.info({ msg: 'Stopped proactive alerts', clientId });
  }
}

export default { startProactiveAlerts, stopProactiveAlerts };
