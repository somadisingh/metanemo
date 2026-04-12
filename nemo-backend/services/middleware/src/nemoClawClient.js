/**
 * NemoClaw HTTP Client
 * Communicates with the NemoClaw agent service
 */

import axios from 'axios';
import logger from './logger.js';

const NEMOCLAW_HOST = process.env.NEMOCLAW_HOST || 'nemoclaw';
const NEMOCLAW_PORT = process.env.NEMOCLAW_PORT || '8090';
const NEMOCLAW_URL = `http://${NEMOCLAW_HOST}:${NEMOCLAW_PORT}`;

const client = axios.create({
  baseURL: NEMOCLAW_URL,
  timeout: 120000,
  headers: {
    'Content-Type': 'application/json'
  }
});

/**
 * Send a multimodal request to the NemoClaw agent.
 * 
 * @param {Object} params - Request parameters
 * @param {string} [params.text] - Transcribed user speech
 * @param {string} [params.image_b64] - Base64 encoded JPEG image
 * @param {number} params.latitude - GPS latitude
 * @param {number} params.longitude - GPS longitude
 * @param {string} [params.timestamp] - ISO timestamp
 * @param {Object} [params.user_profile] - User preferences
 * @returns {Promise<Object>} Agent response
 */
export async function sendToAgent(params) {
  const { text, image_b64, latitude, longitude, timestamp, user_profile } = params;
  
  logger.debug({
    msg: 'Sending request to NemoClaw',
    service: 'nemoclaw',
    method: 'POST /v1/agent',
    hasText: !!text,
    hasImage: !!image_b64,
    latitude,
    longitude
  });
  
  try {
    const response = await client.post('/v1/agent', {
      text,
      image_b64,
      latitude,
      longitude,
      timestamp: timestamp || new Date().toISOString(),
      user_profile
    });
    
    logger.debug({
      msg: 'NemoClaw response received',
      service: 'nemoclaw',
      method: 'POST /v1/agent',
      toolUsed: response.data.tool_used,
      confidence: response.data.confidence
    });
    
    return response.data;
    
  } catch (error) {
    logger.error({
      msg: 'NemoClaw request failed',
      service: 'nemoclaw',
      error: error.message,
      status: error.response?.status
    });
    throw error;
  }
}

/**
 * Health check for NemoClaw service.
 * 
 * @returns {Promise<boolean>} True if healthy
 */
export async function healthCheck() {
  try {
    const response = await client.get('/health', { timeout: 5000 });
    return response.data.status === 'healthy';
  } catch (error) {
    logger.warn({ msg: 'NemoClaw health check failed', error: error.message });
    return false;
  }
}



/**
 * Fetch a full situation report for a GPS location.
 * Calls /v1/situation_report which returns all datasets combined.
 *
 * @param {number} latitude
 * @param {number} longitude
 * @returns {Promise<Object|null>} Situation report or null on error
 */
export async function getSituationReport(latitude, longitude) {
  try {
    const response = await client.post(
      '/v1/situation_report',
      {},
      { params: { latitude, longitude }, timeout: 30000 }
    );
    return response.data;
  } catch (error) {
    logger.warn({ msg: 'Situation report failed', error: error.message, latitude, longitude });
    return null;
  }
}
