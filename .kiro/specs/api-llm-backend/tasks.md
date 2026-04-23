# Implementation Plan: API LLM Backend

## Overview

Replace the local Nemotron NIM GPU inference server with a hosted/cloud LLM API in the NemoClaw agent. This involves updating `agent.py` to swap `call_nemotron()` for a new `call_llm_api()` function with Bearer token auth, reversing the fallback order so the hosted API is primary and Ollama is secondary, updating Docker Compose files to remove GPU-dependent services, and updating configuration files. All external-facing HTTP endpoints remain unchanged.

## Tasks

- [x] 1. Update module-level configuration and add `call_llm_api()`
  - [x] 1.1 Replace Nemotron environment variables with LLM API variables in `agent.py`
    - Remove `NEMOTRON_HOST`, `NEMOTRON_PORT`, `NEMOTRON_MODEL`, `NEMOTRON_ENDPOINT` module-level variables
    - Add `LLM_API_KEY`, `LLM_API_BASE_URL`, `LLM_MODEL_NAME`, `LLM_TIMEOUT_SECONDS`, `LLM_ENDPOINT` module-level variables with defaults per design
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 1.2 Implement `call_llm_api()` function in `agent.py`
    - Create `call_llm_api(messages, max_tokens=150, temperature=0.7)` that sends POST to `LLM_ENDPOINT`
    - Include `Authorization: Bearer {LLM_API_KEY}` header
    - Send body with `model`, `messages`, `max_tokens`, `temperature` keys
    - Use `LLM_TIMEOUT_SECONDS` as the request timeout
    - Return `choices[0].message.content` on success, `None` on failure
    - Log specific messages for 401/403 (auth failure), 429 (rate limited), and other errors (status + body truncated to 200 chars)
    - _Requirements: 1.1, 1.2, 1.4, 1.5, 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 1.3 Remove `call_nemotron()` function from `agent.py`
    - Delete the entire `call_nemotron()` function definition
    - _Requirements: 1.1_

- [x] 2. Update `call_llm()` fallback chain
  - [x] 2.1 Update `call_llm()` to use `call_llm_api()` as primary
    - Replace `call_nemotron()` call with `call_llm_api()` as the first attempt
    - Update fallback log message from "Nemotron failed" to "Hosted LLM API failed"
    - Update final error log from "Both Nemotron and Ollama failed" to "All LLM providers failed"
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ]* 2.2 Write property test for fallback behavior (Property 5)
    - **Property 5: Fallback to Ollama on primary HTTP error**
    - Generate random HTTP error status codes and USE_OLLAMA boolean values
    - Verify Ollama fallback is attempted when USE_OLLAMA=true and skipped when false
    - **Validates: Requirements 3.2, 3.5**

- [x] 3. Update `classify_intent()` to use hosted API as primary
  - [x] 3.1 Update `classify_intent()` call order in `agent.py`
    - Change `call_ollama(messages, ...) or call_nemotron(messages, ...)` to `call_llm_api(messages, ...) or (call_ollama(messages, ...) if USE_OLLAMA else None)`
    - _Requirements: 7.1, 7.2_

- [x] 4. Update `synthesize_response()` to use hosted API
  - [x] 4.1 Update `synthesize_response()` analysis step in `agent.py`
    - Replace `call_nemotron(nemotron_messages, ...)` with `call_llm_api(nemotron_messages, max_tokens=600, temperature=0.3)` for Step 1 (analysis)
    - Add fallback to `call_llm_api()` for Step 2 (summarization) when Ollama returns None or short response
    - _Requirements: 7.3, 7.4, 7.5_

- [x] 5. Update `validate_environment()` to require `LLM_API_KEY`
  - [x] 5.1 Add `LLM_API_KEY` check to `validate_environment()` in `agent.py`
    - Check that `LLM_API_KEY` is set and non-empty
    - Log FATAL if missing or empty
    - Add to `missing` list so agent refuses to start
    - _Requirements: 1.3_

- [x] 6. Checkpoint
  - Ensure all agent.py changes are consistent and the module loads without import errors. Ask the user if questions arise.

- [x] 7. Update Docker Compose and configuration files
  - [x] 7.1 Update `docker-compose.yml`
    - Remove the `nemotron-nim` service definition
    - Remove the `nim-model-cache` volume
    - Remove `depends_on: nemotron-nim` from the `nemoclaw` service
    - Replace `NEMOTRON_NIM_HOST`, `NEMOTRON_NIM_PORT`, `NEMOTRON_MODEL_NAME` env vars in `nemoclaw` with `LLM_API_KEY`, `LLM_API_BASE_URL`, `LLM_MODEL_NAME`, `LLM_TIMEOUT_SECONDS`
    - Add `USE_OLLAMA`, `OLLAMA_HOST`, `OLLAMA_PORT`, `OLLAMA_MODEL` env vars to `nemoclaw`
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [x] 7.2 Update `docker-compose.override.yml`
    - Remove the `nemotron-nim` busybox stub service
    - Replace `NEMOTRON_NIM_HOST`, `NEMOTRON_NIM_PORT`, `NEMOTRON_MODEL_NAME` env vars in `nemoclaw` with `LLM_API_KEY`, `LLM_API_BASE_URL`, `LLM_MODEL_NAME`, `LLM_TIMEOUT_SECONDS`
    - _Requirements: 4.5_

  - [x] 7.3 Update `docker-compose.dev.yml`
    - Remove the `nemotron-inference` service definition
    - Replace `NEMOTRON_NIM_HOST`, `NEMOTRON_NIM_PORT` env vars in `nemoclaw` with `LLM_API_KEY`, `LLM_API_BASE_URL`, `LLM_MODEL_NAME`, `LLM_TIMEOUT_SECONDS`
    - _Requirements: 4.1, 4.4_

  - [x] 7.4 Update `.env.example`
    - Replace the `# ── Nemotron NIM` section with `# ── LLM API` section
    - Add `LLM_API_KEY`, `LLM_API_BASE_URL`, `LLM_MODEL_NAME`, `LLM_TIMEOUT_SECONDS` with defaults and comments
    - Remove `NEMOTRON_NIM_HOST`, `NEMOTRON_NIM_PORT`, `NEMOTRON_MODEL_NAME`
    - Ensure `USE_OLLAMA`, `OLLAMA_HOST`, `OLLAMA_PORT`, `OLLAMA_MODEL` are present
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [x] 7.5 Update `agent_config.yaml`
    - Change `model` field to `nvidia/llama-3.1-nemotron-ultra-253b-v1`
    - Change `nim_endpoint` to `api_endpoint: https://integrate.api.nvidia.com/v1`
    - Retain all existing tool definitions, intent mappings, and sandbox configuration
    - _Requirements: 6.1, 6.2, 6.3_

- [x] 8. Checkpoint
  - Ensure all configuration files are valid. Ask the user if questions arise.

- [ ] 9. Write property-based tests for `call_llm_api()`
  - [ ]* 9.1 Write property test for request body well-formedness (Property 1)
    - **Property 1: Request body is well-formed OpenAI chat completion**
    - Generate random valid messages lists, max_tokens, and temperature values
    - Mock HTTP POST and verify request body contains `model`, `messages`, `max_tokens`, `temperature` with correct values
    - **Validates: Requirements 1.1, 1.4**

  - [ ]* 9.2 Write property test for authorization header (Property 2)
    - **Property 2: Authorization header is correctly formed**
    - Generate random non-empty API key strings
    - Mock HTTP POST and verify `Authorization: Bearer {key}` header
    - **Validates: Requirements 1.2**

  - [ ]* 9.3 Write property test for endpoint URL construction (Property 3)
    - **Property 3: Endpoint URL is correctly constructed**
    - Generate random base URL strings
    - Verify the request URL is `{base_url}/chat/completions`
    - **Validates: Requirements 2.5**

  - [ ]* 9.4 Write property test for return type contract (Property 4)
    - **Property 4: Return type contract**
    - Generate valid API responses and error responses
    - Verify `call_llm_api()` returns string on success, `None` on failure
    - **Validates: Requirements 1.5**

  - [ ]* 9.5 Write property test for timeout configuration (Property 6)
    - **Property 6: Timeout configuration is respected**
    - Generate random positive integer timeout values
    - Mock HTTP POST and verify the timeout parameter matches `LLM_TIMEOUT_SECONDS`
    - **Validates: Requirements 8.1**

  - [ ]* 9.6 Write property test for error logging (Property 7)
    - **Property 7: Error logging includes status and truncated body**
    - Generate random HTTP error status codes and response body strings
    - Verify log output contains the status code and body truncated to 200 chars
    - **Validates: Requirements 8.5**

  - [ ]* 9.7 Write property test for confidence threshold (Property 8)
    - **Property 8: Confidence threshold determines clarification**
    - Generate random float confidence values in [0, 1]
    - Verify `requires_clarification` is true iff confidence < 0.7
    - **Validates: Requirements 9.3**

  - [ ]* 9.8 Write property test for think block stripping (Property 9)
    - **Property 9: Think block stripping**
    - Generate strings with embedded `<think>...</think>` blocks
    - Verify all think blocks are removed while preserving surrounding text
    - **Validates: Requirements 9.4**

- [x] 10. Update existing tests to use `call_llm_api` mock
  - [x] 10.1 Update test mocks in `test_agent.py`
    - Change `@patch('agent.call_nemotron')` to `@patch('agent.call_llm_api')` in `TestAgentIntegration`
    - Verify all existing test assertions remain unchanged
    - _Requirements: 10.1, 10.2, 10.3_

- [x] 11. Final checkpoint
  - Run the full test suite to ensure all tests pass. Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The project already has `hypothesis` in `requirements.txt` for property-based testing
- All code changes are in Python targeting `nemo-backend/services/nemoclaw/`
