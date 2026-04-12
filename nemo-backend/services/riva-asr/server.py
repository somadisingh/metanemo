"""
Parakeet ASR Server - HTTP endpoint wrapping NeMo Parakeet TDT 0.6B
Accepts PCM audio (16kHz, 16-bit, mono) and returns transcript text.
Exposes POST /transcribe with multipart audio or raw bytes.
"""

import os
import io
import logging
import tempfile
import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Parakeet ASR", version="1.0.0")

MODEL_PATH = os.environ.get(
    "ASR_MODEL_PATH",
    "/home/acergn100_24/models/llm/nvidia--parakeet-tdt-0.6b-v3/parakeet-tdt-0.6b-v3.nemo"
)

asr_model = None


def load_model():
    global asr_model
    try:
        import nemo.collections.asr as nemo_asr
        logger.info(f"Loading Parakeet from {MODEL_PATH}")
        asr_model = nemo_asr.models.ASRModel.restore_from(
            MODEL_PATH, map_location="cuda" if __import__("torch").cuda.is_available() else "cpu"
        )
        asr_model.eval()
        logger.info("Parakeet loaded successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to load Parakeet: {e}")
        return False


@app.on_event("startup")
async def startup():
    if not load_model():
        logger.warning("ASR model not loaded - running in mock mode")


@app.get("/health")
async def health():
    return {"status": "healthy" if asr_model else "degraded",
            "model_loaded": asr_model is not None,
            "service": "parakeet-asr"}


@app.post("/transcribe")
async def transcribe(request: Request):
    """
    Accept raw PCM bytes (16kHz, 16-bit, mono) and return transcript.
    Content-Type: application/octet-stream
    """
    if asr_model is None:
        # Mock mode
        return JSONResponse({"transcript": "", "is_final": True})

    try:
        pcm_bytes = await request.body()
        if not pcm_bytes:
            return JSONResponse({"transcript": "", "is_final": True})

        # Convert PCM int16 bytes to float32 numpy array
        audio_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0

        # Need at least 0.1s of audio (1600 samples at 16kHz)
        if len(audio_float32) < 1600:
            return JSONResponse({"transcript": "", "is_final": False})

        # Write to temp WAV file (NeMo expects file path)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, audio_float32, 16000, format="WAV", subtype="PCM_16")
            tmp_path = f.name

        try:
            transcripts = asr_model.transcribe([tmp_path])
            transcript = transcripts[0] if transcripts else ""
            logger.info(f"Transcript: {repr(transcript)}")
            return JSONResponse({"transcript": transcript, "is_final": True})
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=50051)
