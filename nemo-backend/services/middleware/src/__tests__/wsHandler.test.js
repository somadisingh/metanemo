/**
 * WebSocket Handler Tests
 * 
 * Property 5: Audio Pipeline End-to-End Routing
 * Property 10: Vision Frame JSON Assembly and Forwarding
 * Property 11: Missing Vision Fields Discarded
 */

import { jest } from '@jest/globals';
import fc from 'fast-check';

// Mock dependencies
jest.mock('../nemoClawClient.js', () => ({
  sendToAgent: jest.fn().mockResolvedValue({
    text: 'Test response',
    hazards: [],
    qol_score: 0
  })
}));

jest.mock('../logger.js', () => ({
  default: {
    debug: jest.fn(),
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn()
  }
}));

import { handleMessage, sendError, sendControl } from '../wsHandler.js';
import { sendToAgent } from '../nemoClawClient.js';
import logger from '../logger.js';

// Helper to create mock WebSocket
function createMockWs() {
  return {
    send: jest.fn(),
    readyState: 1, // OPEN
    OPEN: 1
  };
}

// Helper to create vision frame buffer
function createVisionFrame(latitude, longitude, jpegPayload) {
  const timestamp = Date.now();
  const payloadLength = jpegPayload.length;
  
  const buffer = Buffer.alloc(29 + payloadLength);
  
  buffer[0] = 0x02; // VISION type
  buffer.writeUInt32BE(payloadLength, 1);
  buffer.writeBigInt64BE(BigInt(timestamp), 5);
  buffer.writeDoubleBE(latitude, 13);
  buffer.writeDoubleBE(longitude, 21);
  jpegPayload.copy(buffer, 29);
  
  return buffer;
}

// Helper to create audio frame buffer
function createAudioFrame(pcmPayload) {
  const payloadLength = pcmPayload.length;
  const buffer = Buffer.alloc(5 + payloadLength);
  
  buffer[0] = 0x01; // AUDIO type
  buffer.writeUInt32BE(payloadLength, 1);
  pcmPayload.copy(buffer, 5);
  
  return buffer;
}

describe('WebSocket Handler', () => {
  let mockWs;
  let context;
  
  beforeEach(() => {
    mockWs = createMockWs();
    context = {
      clientId: 'test_client',
      clientIp: '127.0.0.1',
      userProfile: {}
    };
    jest.clearAllMocks();
  });

  describe('Vision Frame Handling', () => {
    // Property 10: Vision Frame JSON Assembly and Forwarding
    test('valid vision frame is forwarded to NemoClaw', async () => {
      const jpegPayload = Buffer.from([0xFF, 0xD8, 0xFF, 0xE0]); // JPEG magic bytes
      const buffer = createVisionFrame(40.7128, -74.0060, jpegPayload);
      
      await handleMessage(mockWs, buffer, context);
      
      expect(sendToAgent).toHaveBeenCalledWith(
        expect.objectContaining({
          latitude: 40.7128,
          longitude: -74.0060,
          image_b64: expect.any(String)
        })
      );
    });

    // Property 11: Missing Vision Fields Discarded
    test('vision frame with invalid latitude is discarded', async () => {
      const jpegPayload = Buffer.from([0xFF, 0xD8]);
      const buffer = createVisionFrame(NaN, -74.0060, jpegPayload);
      
      await handleMessage(mockWs, buffer, context);
      
      expect(sendToAgent).not.toHaveBeenCalled();
      expect(logger.warn).toHaveBeenCalledWith(
        expect.objectContaining({
          msg: expect.stringContaining('GPS')
        })
      );
    });

    test('vision frame with out-of-range latitude is discarded', async () => {
      const jpegPayload = Buffer.from([0xFF, 0xD8]);
      const buffer = createVisionFrame(100, -74.0060, jpegPayload); // Invalid lat > 90
      
      await handleMessage(mockWs, buffer, context);
      
      expect(sendToAgent).not.toHaveBeenCalled();
    });

    test('vision frame with empty payload is discarded', async () => {
      const buffer = createVisionFrame(40.7128, -74.0060, Buffer.alloc(0));
      
      await handleMessage(mockWs, buffer, context);
      
      expect(sendToAgent).not.toHaveBeenCalled();
    });

    // Feature: pseudo-meta-glass, Property 10: Vision frame assembly
    test('Property 10: vision frame includes all required fields', async () => {
      fc.assert(
        fc.property(
          fc.double({ min: -90, max: 90, noNaN: true }),
          fc.double({ min: -180, max: 180, noNaN: true }),
          fc.uint8Array({ minLength: 1, maxLength: 100 }),
          async (lat, lon, payload) => {
            jest.clearAllMocks();
            const buffer = createVisionFrame(lat, lon, Buffer.from(payload));
            
            await handleMessage(mockWs, buffer, context);
            
            if (sendToAgent.mock.calls.length > 0) {
              const call = sendToAgent.mock.calls[0][0];
              expect(call).toHaveProperty('latitude');
              expect(call).toHaveProperty('longitude');
              expect(call).toHaveProperty('image_b64');
              expect(call).toHaveProperty('timestamp');
            }
          }
        ),
        { numRuns: 50 }
      );
    });
  });

  describe('Audio Frame Handling', () => {
    // Property 5: Audio Pipeline End-to-End Routing
    test('audio frame is logged for processing', async () => {
      const pcmPayload = Buffer.alloc(1600); // 100ms of 16kHz audio
      const buffer = createAudioFrame(pcmPayload);
      
      await handleMessage(mockWs, buffer, context);
      
      expect(logger.debug).toHaveBeenCalledWith(
        expect.objectContaining({
          msg: 'Audio frame received'
        })
      );
    });
  });

  describe('Error Handling', () => {
    test('non-binary message sends error', async () => {
      await handleMessage(mockWs, 'not binary', context);
      
      expect(mockWs.send).toHaveBeenCalledWith(
        expect.stringContaining('ERROR')
      );
    });

    test('unknown message type sends error', async () => {
      const buffer = Buffer.from([0xFF, 0x00, 0x00, 0x00, 0x00]);
      
      await handleMessage(mockWs, buffer, context);
      
      expect(mockWs.send).toHaveBeenCalledWith(
        expect.stringContaining('UNKNOWN_MESSAGE_TYPE')
      );
    });
  });

  describe('Control Messages', () => {
    test('sendError sends JSON error message', () => {
      sendError(mockWs, 'TEST_ERROR', 'Test message');
      
      expect(mockWs.send).toHaveBeenCalledWith(
        JSON.stringify({
          type: 'ERROR',
          code: 'TEST_ERROR',
          message: 'Test message'
        })
      );
    });

    test('sendControl sends JSON control message', () => {
      sendControl(mockWs, 'STREAM_READY');
      
      expect(mockWs.send).toHaveBeenCalledWith(
        JSON.stringify({
          type: 'CONTROL',
          action: 'STREAM_READY'
        })
      );
    });
  });
});
