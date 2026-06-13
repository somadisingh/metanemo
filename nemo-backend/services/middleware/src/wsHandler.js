/**
 * WebSocket Handler — Acer Veriton environment Compatible
 *
 * Implements the Acer Veriton environment API protocol so the iOS app works unchanged.
 * Accepts JSON text messages (same format as Acer Veriton environment API).
 * Responds with JSON text messages (same format as Acer Veriton environment API).
 *
 * Protocol:
 *   Client → Server (JSON text):
 *     { setup: { model, generationConfig, systemInstruction, ... } }
 *     { realtimeInput: { audio: { mimeType, data: base64 } } }
 *     { realtimeInput: { video: { mimeType, data: base64 } } }
 *     { clientContent: { turns: [{ role, parts }] } }
 *
 *   Server → Client (JSON text):
 *     { setupComplete: {} }
 *     { serverContent: { modelTurn: { parts: [{ inlineData: { mimeType, data } }] } } }
 *     { serverContent: { turnComplete: true } }
 *     { serverContent: { outputTranscription: { text } } }
 */

import logger from './logger.js';
import cooldownCache from './cooldownCache.js';
import { sendToAgent } from './nemoClawClient.js';
import { startProactiveAlerts, stopProactiveAlerts } from './proactiveAlerts.js';
import { processAudio, clearBuffer, flushBuffer } from './asrClient.js';

// VAD: silence threshold — if no audio for this many ms, treat as end of utterance
const SILENCE_THRESHOLD_MS = 1500;
const PROACTIVE_ALERTS_ENABLED = (process.env.PROACTIVE_ALERTS_ENABLED ?? 'true').toLowerCase() === 'true';

// Per-client state
export const clientState = new Map();

function getState(clientId) {
  if (!clientState.has(clientId)) {
    clientState.set(clientId, {
      latitude: 40.7128,
      longitude: -74.0060,
      systemInstruction: '',
      silenceTimer: null,
      pendingTranscript: '',
      isProcessing: false,
      setupDone: false,
      hasReceivedGPS: false
    });
  }
  return clientState.get(clientId);
}

function clearState(clientId) {
  const state = clientState.get(clientId);
  if (state?.silenceTimer) clearTimeout(state.silenceTimer);
  clientState.delete(clientId);
  clearBuffer(clientId);
}

/**
 * Send a JSON message to the client
 */
function sendJSON(ws, obj) {
  if (ws.readyState === ws.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}


/**
 * Send text transcription (what the AI said)
 */
function sendOutputTranscription(ws, text) {
  sendJSON(ws, {
    serverContent: {
      outputTranscription: { text }
    }
  });
}

/**
 * Send input transcription (what the user said)
 */
function sendInputTranscription(ws, text) {
  sendJSON(ws, {
    serverContent: {
      inputTranscription: { text }
    }
  });
}

/**
 * Send turn complete
 */
function sendTurnComplete(ws) {
  sendJSON(ws, {
    serverContent: { turnComplete: true }
  });
}

/**
 * Process a completed utterance: ASR transcript → NemoClaw → send text response
 */
async function processUtterance(ws, clientId, transcript) {
  const state = getState(clientId);
  if (state.isProcessing) return;
  state.isProcessing = true;

  logger.info({ msg: 'Processing utterance', clientId, transcript });

  // Echo back what user said
  sendInputTranscription(ws, transcript);

  try {
    // Call NemoClaw agent
    const response = await sendToAgent({
      text: transcript,
      latitude: state.latitude,
      longitude: state.longitude
    });

    if (!response?.text) {
      state.isProcessing = false;
      return;
    }

    const responseText = response.text;
    logger.info({ msg: 'Agent response', clientId, text: responseText.slice(0, 100) });

    // Check cooldown for hazards
    const hazards = response.hazards || [];
    const unsuppressedHazards = hazards.filter(h => {
      const hazardId = h.id || `${h.type}_${(h.description || '').slice(0, 30)}`;
      if (cooldownCache.isSuppressed(hazardId)) return false;
      cooldownCache.record(hazardId);
      return true;
    });

    if (unsuppressedHazards.length === 0 && hazards.length > 0) {
      // Cooldown suppressed — send text only
      sendOutputTranscription(ws, responseText);
      sendTurnComplete(ws);
      state.isProcessing = false;
      return;
    }

    // Send text transcription of AI response
    sendOutputTranscription(ws, responseText);

    sendTurnComplete(ws);

  } catch (err) {
    logger.error({ msg: 'Agent error', clientId, error: err.message });
    sendTurnComplete(ws);
  } finally {
    state.isProcessing = false;
  }
}

/**
 * Handle audio input — accumulate and use VAD silence detection
 */
async function handleAudioInput(ws, clientId, base64Audio) {
  const state = getState(clientId);
  const pcmData = Buffer.from(base64Audio, 'base64');

  // Reset silence timer on each audio chunk
  if (state.silenceTimer) {
    clearTimeout(state.silenceTimer);
    state.silenceTimer = null;
  }

  // Accumulate audio for ASR
  await processAudio(clientId, pcmData, async (transcript) => {
    if (transcript && transcript.trim().length > 2) {
      state.pendingTranscript = transcript.trim();
    }
  });

  // Set silence timer — when user stops speaking, flush buffer and process
  state.silenceTimer = setTimeout(async () => {
    state.silenceTimer = null;
    // Flush any remaining audio (catches short utterances under MIN_AUDIO_BYTES)
    await flushBuffer(clientId, async (transcript) => {
      if (transcript && transcript.trim().length > 2) {
        state.pendingTranscript = transcript.trim();
      }
    });
    if (state.pendingTranscript && !state.isProcessing) {
      const transcript = state.pendingTranscript;
      state.pendingTranscript = '';
      await processUtterance(ws, clientId, transcript);
    }
  }, SILENCE_THRESHOLD_MS);
}

/**
 * Handle video/image input — send to NemoClaw with GPS
 */
async function handleVideoInput(ws, clientId, base64Image) {
  const state = getState(clientId);
  if (state.isProcessing) return;
  state.isProcessing = true;

  try {
    const response = await sendToAgent({
      image_b64: base64Image,
      latitude: state.latitude,
      longitude: state.longitude
    });

    if (!response?.text) {
      state.isProcessing = false;
      return;
    }

    const hazards = response.hazards || [];
    const unsuppressedHazards = hazards.filter(h => {
      const hazardId = h.id || `${h.type}_${state.latitude}_${state.longitude}`;
      if (cooldownCache.isSuppressed(hazardId)) return false;
      cooldownCache.record(hazardId);
      return true;
    });

    if (unsuppressedHazards.length === 0 && hazards.length > 0) {
      sendOutputTranscription(ws, response.text);
      sendTurnComplete(ws);
      state.isProcessing = false;
      return;
    }

    sendOutputTranscription(ws, response.text);
    sendTurnComplete(ws);
  } catch (err) {
    logger.error({ msg: 'Vision agent error', clientId, error: err.message });
    sendTurnComplete(ws);
  } finally {
    state.isProcessing = false;
  }
}

/**
 * Handle text message from client (clientContent)
 */
async function handleTextInput(ws, clientId, text) {
  await processUtterance(ws, clientId, text);
}

/**
 * Main message handler
 */
export async function handleMessage(ws, message, context) {
  const { clientId } = context;

  // Handle both text JSON and binary frames
  let jsonStr;
  if (typeof message === 'string') {
    jsonStr = message;
  } else if (Buffer.isBuffer(message)) {
    // Legacy binary protocol support
    if (message.length > 0 && message[0] === 0x01) {
      // Binary audio frame — convert to base64 and process
      const pcmData = message.slice(5);
      const state = getState(clientId);
      if (state.silenceTimer) clearTimeout(state.silenceTimer);
      await processAudio(clientId, pcmData, async (transcript) => {
        if (transcript?.trim().length > 2) state.pendingTranscript = transcript.trim();
      });
      state.silenceTimer = setTimeout(async () => {
        state.silenceTimer = null;
        await flushBuffer(clientId, async (transcript) => {
          if (transcript?.trim().length > 2) state.pendingTranscript = transcript.trim();
        });
        if (state.pendingTranscript && !state.isProcessing) {
          const t = state.pendingTranscript;
          state.pendingTranscript = '';
          await processUtterance(ws, clientId, t);
        }
      }, SILENCE_THRESHOLD_MS);
      return;
    }
    jsonStr = message.toString('utf8');
  } else {
    return;
  }

  let json;
  try {
    json = JSON.parse(jsonStr);
  } catch {
    logger.warn({ msg: 'Invalid JSON from client', clientId });
    return;
  }

  const state = getState(clientId);

  // Setup message
  if (json.setup) {
    logger.info({ msg: 'Setup received', clientId, model: json.setup.model });
    if (json.setup.systemInstruction?.parts?.[0]?.text) {
      state.systemInstruction = json.setup.systemInstruction.parts[0].text;
    }
    sendJSON(ws, { setupComplete: {} });
    state.setupDone = true;
    logger.info({ msg: 'Setup complete sent', clientId });
    return;
  }

  // Realtime input
  if (json.realtimeInput) {
    const input = json.realtimeInput;

    if (input.audio?.data) {
      logger.debug({ msg: 'Audio input received', clientId, bytes: input.audio.data.length });
      await handleAudioInput(ws, clientId, input.audio.data);
    }

    if (input.video?.data) {
      logger.debug({ msg: 'Video input received', clientId });
      await handleVideoInput(ws, clientId, input.video.data);
    }

    // GPS location update (custom extension)
    if (input.location) {
      state.latitude = input.location.latitude || state.latitude;
      state.longitude = input.location.longitude || state.longitude;
      state.hasReceivedGPS = true;
    }
    return;
  }

  // Client content (text message)
  if (json.clientContent?.turns) {
    const turns = json.clientContent.turns;
    const userTurn = turns.find(t => t.role === 'user');
    const text = userTurn?.parts?.map(p => p.text).filter(Boolean).join(' ');
    if (text) {
      logger.info({ msg: 'Text input received', clientId, text: text.slice(0, 100) });
      await handleTextInput(ws, clientId, text);
    }
    return;
  }

  // Tool response (not used — NemoClaw handles tools server-side)
  if (json.toolResponse) {
    return;
  }

  logger.debug({ msg: 'Unknown message type', clientId, keys: Object.keys(json) });
}

export function handleConnection(ws, req) {
  // Defense-in-depth: verify auth token even though verifyClient should have checked
  const authToken = process.env.WS_AUTH_TOKEN;
  if (!authToken) {
    logger.warn({ msg: 'Connection rejected — WS_AUTH_TOKEN not configured' });
    ws.close(1008, 'Unauthorized');
    return;
  }

  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  const queryToken = url.searchParams.get('token');
  const authHeader = req.headers['authorization'];
  let providedToken = null;

  if (authHeader && authHeader.startsWith('Bearer ')) {
    providedToken = authHeader.slice(7);
  } else if (queryToken) {
    providedToken = queryToken;
  }

  if (!providedToken || providedToken !== authToken) {
    logger.warn({ msg: 'Connection rejected — invalid auth token', ip: req.socket.remoteAddress });
    ws.close(1008, 'Unauthorized');
    return;
  }

  const clientId = `client_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  const clientIp = req.socket.remoteAddress;

  logger.info({ msg: 'Client connected', clientId, clientIp });

  const context = { clientId, clientIp };

  // Track ws connection for admin triggers
  wsConnections.set(clientId, ws);

  // Start proactive alert timer only when explicitly enabled.
  if (PROACTIVE_ALERTS_ENABLED) {
    startProactiveAlerts(ws, clientId, getState);
  } else {
    logger.info({ msg: 'Proactive alerts disabled', clientId });
  }

  ws.on('message', (message) => handleMessage(ws, message, context));

  ws.on('close', () => {
    logger.info({ msg: 'Client disconnected', clientId });
    stopProactiveAlerts(clientId);
    wsConnections.delete(clientId);
    clearState(clientId);
  });

  ws.on('error', (error) => {
    logger.error({ msg: 'WebSocket error', clientId, error: error.message });
  });
}

export function sendError(ws, code, message) {
  if (ws.readyState === ws.OPEN) {
    ws.send(JSON.stringify({ error: { code, message } }));
  }
}

export function sendControl(ws, action) {
}

// Export for admin trigger endpoint
export const wsConnections = new Map(); // clientId -> ws

// Export for admin trigger endpoint

export default { handleMessage, handleConnection, sendError, sendControl };
