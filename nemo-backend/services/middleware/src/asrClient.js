/**
 * Parakeet ASR Client
 * Accumulates PCM audio chunks per client and sends to local ASR server.
 */

import http from 'node:http';
import logger from './logger.js';

const ASR_HOST = process.env.RIVA_ASR_HOST || 'host.docker.internal';
const ASR_PORT = process.env.RIVA_ASR_PORT || '50051';

// Accumulate ~1s of audio before sending (16000 samples * 2 bytes = 32000 bytes)
const MIN_AUDIO_BYTES = 16000; // 0.5s at 16kHz 16-bit mono

const audioBuffers = new Map();

/**
 * Accumulate PCM audio for a client and transcribe when enough is buffered.
 */
export async function processAudio(clientId, pcmData, onTranscript) {
  const existing = audioBuffers.get(clientId) || Buffer.alloc(0);
  const combined = Buffer.concat([existing, pcmData]);
  audioBuffers.set(clientId, combined);

  if (combined.length < MIN_AUDIO_BYTES) return;

  const audioToTranscribe = combined;
  audioBuffers.delete(clientId);

  try {
    logger.debug({ msg: 'Sending audio to ASR', service: 'asr', method: 'POST /transcribe', clientId, bytes: audioToTranscribe.length });
    const transcript = await transcribeAudio(audioToTranscribe);
    if (transcript && transcript.trim().length > 0) {
      logger.info({ msg: 'ASR transcript', clientId, transcript });
      await onTranscript(transcript.trim());
    }
  } catch (err) {
    logger.error({ msg: 'ASR error', clientId, error: err.message });
  }
}

async function transcribeAudio(pcmBuffer) {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: ASR_HOST,
      port: parseInt(ASR_PORT),
      path: '/transcribe',
      method: 'POST',
      headers: {
        'Content-Type': 'application/octet-stream',
        'Content-Length': pcmBuffer.length
      }
    };

    const req = http.request(options, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        try {
          const json = JSON.parse(data);
          resolve(json.transcript || '');
        } catch (e) {
          reject(new Error('Invalid ASR response: ' + data));
        }
      });
    });

    req.on('error', reject);
    req.setTimeout(15000, () => { req.destroy(); reject(new Error('ASR timeout')); });
    req.write(pcmBuffer);
    req.end();
  });
}

export function clearBuffer(clientId) {
  audioBuffers.delete(clientId);
}

/**
 * Flush whatever audio is buffered (even if under MIN_AUDIO_BYTES) and transcribe.
 * Called when silence is detected to catch short utterances.
 */
export async function flushBuffer(clientId, onTranscript) {
  const buffered = audioBuffers.get(clientId);
  if (!buffered || buffered.length < 3200) { // need at least 0.1s
    audioBuffers.delete(clientId);
    return;
  }
  audioBuffers.delete(clientId);
  try {
    const transcript = await transcribeAudio(buffered);
    if (transcript && transcript.trim().length > 0) {
      await onTranscript(transcript.trim());
    }
  } catch (err) {
    // Ignore flush errors
  }
}

export default { processAudio, clearBuffer, flushBuffer };
