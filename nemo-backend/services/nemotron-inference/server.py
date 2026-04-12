"""
Nemotron Inference Server - OpenAI-compatible API for local Nemotron model.
Loads NVIDIA Nemotron-3-Nano-30B from local safetensors.
"""

import os
import logging
import time
from typing import List, Optional
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Nemotron Inference", version="1.0.0")

# Model configuration
MODEL_PATH = os.environ.get('MODEL_PATH', '/models/nvidia--NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4')
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MAX_NEW_TOKENS = int(os.environ.get('MAX_NEW_TOKENS', '256'))

# Global model and tokenizer
model = None
tokenizer = None


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "nemotron-3-nano"
    messages: List[Message]
    max_tokens: Optional[int] = 256
    temperature: Optional[float] = 0.7
    stream: Optional[bool] = False


class ChatChoice(BaseModel):
    index: int
    message: Message
    finish_reason: str


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatChoice]
    usage: Usage


def load_model():
    """Load Nemotron model and tokenizer."""
    global model, tokenizer
    
    logger.info(f"Loading model from {MODEL_PATH}")
    logger.info(f"Device: {DEVICE}")
    
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            MODEL_PATH,
            trust_remote_code=True
        )
        
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_PATH,
            torch_dtype=torch.float16 if DEVICE == "cuda" else torch.float32,
            device_map="auto" if DEVICE == "cuda" else None,
            trust_remote_code=True,
            low_cpu_mem_usage=True,
            ignore_mismatched_sizes=True
        )
        
        if DEVICE == "cpu":
            model = model.to(DEVICE)
        
        logger.info("Model loaded successfully")
        return True
        
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        return False


@app.on_event("startup")
async def startup():
    """Load model on startup."""
    if not load_model():
        logger.warning("Model not loaded - running in mock mode")


@app.get("/")
async def root():
    return {"service": "Nemotron Inference", "model": MODEL_PATH, "device": DEVICE}


@app.get("/health")
async def health():
    return {
        "status": "healthy" if model is not None else "degraded",
        "model_loaded": model is not None,
        "device": DEVICE
    }


@app.get("/v1/models")
async def list_models():
    """OpenAI-compatible models endpoint."""
    return {
        "object": "list",
        "data": [
            {
                "id": "nemotron-3-nano",
                "object": "model",
                "owned_by": "nvidia"
            }
        ]
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest):
    """OpenAI-compatible chat completions endpoint."""
    
    if model is None or tokenizer is None:
        # Mock response when model not loaded
        return ChatResponse(
            id=f"chatcmpl-{int(time.time())}",
            created=int(time.time()),
            model=request.model,
            choices=[
                ChatChoice(
                    index=0,
                    message=Message(
                        role="assistant",
                        content='{"intent": "safety", "confidence": 0.8, "tool": "cold_query", "clarification_needed": false}'
                    ),
                    finish_reason="stop"
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        )
    
    try:
        # Format messages for the model
        prompt = ""
        for msg in request.messages:
            if msg.role == "system":
                prompt += f"<|system|>\n{msg.content}\n"
            elif msg.role == "user":
                prompt += f"<|user|>\n{msg.content}\n"
            elif msg.role == "assistant":
                prompt += f"<|assistant|>\n{msg.content}\n"
        prompt += "<|assistant|>\n"
        
        # Tokenize
        inputs = tokenizer(prompt, return_tensors="pt").to(DEVICE)
        prompt_tokens = inputs.input_ids.shape[1]
        
        # Generate
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=request.max_tokens or MAX_NEW_TOKENS,
                temperature=request.temperature or 0.7,
                do_sample=True,
                pad_token_id=tokenizer.eos_token_id
            )
        
        # Decode
        generated = outputs[0][prompt_tokens:]
        response_text = tokenizer.decode(generated, skip_special_tokens=True)
        completion_tokens = len(generated)
        
        return ChatResponse(
            id=f"chatcmpl-{int(time.time())}",
            created=int(time.time()),
            model=request.model,
            choices=[
                ChatChoice(
                    index=0,
                    message=Message(role="assistant", content=response_text),
                    finish_reason="stop"
                )
            ],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens
            )
        )
        
    except Exception as e:
        logger.error(f"Generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
