"""
TTS Server - HTTP endpoint using NeMo FastPitch + HifiGAN
Accepts text and returns WAV audio (22050Hz, 16-bit, mono).
POST /synthesize {"text": "..."}
Numbers are converted to words before synthesis.
"""

import os
import io
import re
import logging
import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="NeMo TTS", version="1.0.0")

SAMPLE_RATE = 22050
tts_model = None
vocoder_model = None


def load_model():
    global tts_model, vocoder_model
    try:
        import nemo.collections.tts as nemo_tts
        logger.info("Loading FastPitch TTS (pretrained en-US)...")
        tts_model = nemo_tts.models.FastPitchModel.from_pretrained("tts_en_fastpitch")
        tts_model.eval()
        logger.info("Loading HifiGAN vocoder...")
        vocoder_model = nemo_tts.models.HifiGanModel.from_pretrained("tts_en_hifigan")
        vocoder_model.eval()
        logger.info("TTS models loaded successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to load TTS: {e}")
        return False


@app.on_event("startup")
async def startup():
    if not load_model():
        logger.warning("TTS model not loaded - running in mock mode")


@app.get("/health")
async def health():
    return {
        "status": "healthy" if tts_model else "degraded",
        "model_loaded": tts_model is not None,
        "service": "nemo-tts"
    }


# ── Number-to-words conversion ──────────────────────────────────────────────

_ONES = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight',
         'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen',
         'sixteen', 'seventeen', 'eighteen', 'nineteen']
_TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty',
         'sixty', 'seventy', 'eighty', 'ninety']


def _int_to_words(n: int) -> str:
    if n < 0:
        return 'negative ' + _int_to_words(-n)
    if n == 0:
        return 'zero'
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ((' ' + _ONES[n % 10]) if n % 10 else '')
    if n < 1000:
        return _ONES[n // 100] + ' hundred' + ((' ' + _int_to_words(n % 100)) if n % 100 else '')
    if n < 1_000_000:
        return _int_to_words(n // 1000) + ' thousand' + ((' ' + _int_to_words(n % 1000)) if n % 1000 else '')
    return str(n)


def num_to_words(text: str) -> str:
    """Replace all digit sequences with spoken words."""
    def replace(m):
        try:
            return _int_to_words(int(m.group(0)))
        except ValueError:
            return m.group(0)
    return re.sub(r'[0-9]+', replace, text)


# ── Synthesize endpoint ──────────────────────────────────────────────────────

class SynthesizeRequest(BaseModel):
    text: str
    language: str = "en"


@app.post("/synthesize")
async def synthesize(req: SynthesizeRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Empty text")

    if tts_model is None or vocoder_model is None:
        silence = np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.float32)
        buf = io.BytesIO()
        sf.write(buf, silence, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return Response(content=buf.getvalue(), media_type="audio/wav")

    try:
        import torch
        spoken_text = num_to_words(req.text)
        logger.info(f"Synthesizing: {repr(spoken_text[:80])}")

        with torch.no_grad():
            parsed = tts_model.parse(spoken_text)
            spectrogram = tts_model.generate_spectrogram(tokens=parsed)
            audio = vocoder_model.convert_spectrogram_to_audio(spec=spectrogram)

        audio_np = audio.squeeze().cpu().numpy().astype(np.float32)
        max_val = np.abs(audio_np).max()
        if max_val > 0:
            audio_np = audio_np / max_val * 0.95

        buf = io.BytesIO()
        sf.write(buf, audio_np, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        wav_bytes = buf.getvalue()

        logger.info(f"Synthesized {len(wav_bytes)} bytes")
        return Response(content=wav_bytes, media_type="audio/wav")

    except Exception as e:
        logger.error(f"Synthesis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=50052)
