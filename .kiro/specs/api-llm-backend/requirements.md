# Requirements Document

## Introduction

The NemoClaw backend currently relies on a local Nemotron NIM inference server running on an NVIDIA DGX machine with GPU to serve the Nemotron-3-Nano-30B model. The DGX machine is no longer available. This feature reconfigures the backend to use a hosted/cloud LLM API (such as NVIDIA NIM API, OpenAI API, or other OpenAI-compatible endpoints) instead of the local inference server. The change is scoped to the backend inference path only — the middleware, iOS app, and tool-dispatch logic remain unchanged.

## Glossary

- **NemoClaw_Agent**: The Python FastAPI service (`services/nemoclaw/agent.py`) that classifies user intent, dispatches tools, and synthesizes natural-language responses.
- **LLM_Client**: The module responsible for making HTTP requests to an LLM inference endpoint (replacing the current `call_nemotron` and `call_ollama` functions).
- **API_Provider**: A hosted/cloud LLM service that exposes an OpenAI-compatible `/v1/chat/completions` endpoint (e.g., NVIDIA NIM API, OpenAI, Together AI, Groq).
- **API_Key**: A secret credential used to authenticate requests to the API_Provider.
- **Fallback_Chain**: The ordered sequence of LLM backends the NemoClaw_Agent tries when a request fails, proceeding to the next backend on error.
- **Classification_Request**: An LLM call made by `classify_intent()` to determine user intent from spoken text.
- **Synthesis_Request**: An LLM call made by `synthesize_response()` to convert tool results into natural-language output.
- **Nemotron_NIM_Service**: The Docker Compose service (`nemotron-nim`) that runs the local GPU-based Nemotron inference server.
- **Agent_Config**: The YAML configuration file (`agent_config.yaml`) that defines the agent's model, endpoint, tools, and intents.

## Requirements

### Requirement 1: Unified LLM Client with API Key Authentication

**User Story:** As a backend developer, I want a single LLM client function that calls a hosted API with API key authentication, so that I can replace the local Nemotron NIM server without changing the rest of the agent logic.

#### Acceptance Criteria

1. THE LLM_Client SHALL send requests to the API_Provider using the OpenAI-compatible `/v1/chat/completions` HTTP POST format.
2. THE LLM_Client SHALL include the API_Key in an `Authorization: Bearer <key>` header on every request to the API_Provider.
3. WHEN the `LLM_API_KEY` environment variable is not set or is empty, THE NemoClaw_Agent SHALL log an error at startup and refuse to start.
4. THE LLM_Client SHALL accept the same `messages`, `max_tokens`, and `temperature` parameters as the current `call_nemotron` function.
5. THE LLM_Client SHALL return the assistant message content string on success, or `None` on failure.

### Requirement 2: Configurable API Endpoint and Model

**User Story:** As a backend developer, I want to configure the API endpoint URL and model name via environment variables, so that I can switch between different hosted LLM providers without code changes.

#### Acceptance Criteria

1. THE NemoClaw_Agent SHALL read the API endpoint base URL from the `LLM_API_BASE_URL` environment variable.
2. THE NemoClaw_Agent SHALL read the model identifier from the `LLM_MODEL_NAME` environment variable.
3. WHEN `LLM_API_BASE_URL` is not set, THE NemoClaw_Agent SHALL default to `https://integrate.api.nvidia.com/v1`.
4. WHEN `LLM_MODEL_NAME` is not set, THE NemoClaw_Agent SHALL default to `nvidia/llama-3.1-nemotron-ultra-253b-v1`.
5. THE LLM_Client SHALL append `/chat/completions` to the `LLM_API_BASE_URL` to form the full request URL.

### Requirement 3: Fallback Chain for LLM Calls

**User Story:** As a backend developer, I want the agent to fall back to a secondary LLM provider when the primary API is unavailable, so that the service remains operational during provider outages.

#### Acceptance Criteria

1. THE NemoClaw_Agent SHALL attempt the primary API_Provider first for every LLM call.
2. WHEN the primary API_Provider returns an HTTP error or times out, THE NemoClaw_Agent SHALL attempt the secondary provider (Ollama) if `USE_OLLAMA` is set to `true`.
3. WHEN both the primary API_Provider and Ollama fail, THE NemoClaw_Agent SHALL return `None` from the LLM_Client.
4. THE NemoClaw_Agent SHALL log a warning message when falling back from the primary API_Provider to Ollama.
5. IF the `USE_OLLAMA` environment variable is set to `false`, THEN THE NemoClaw_Agent SHALL not attempt the Ollama fallback.

### Requirement 4: Replace Local Nemotron NIM Service in Docker Compose

**User Story:** As a DevOps engineer, I want the Docker Compose configuration to remove the GPU-dependent Nemotron NIM service, so that the backend can run on any machine without an NVIDIA GPU.

#### Acceptance Criteria

1. THE Docker Compose configuration SHALL remove the `nemotron-nim` service definition from `docker-compose.yml`.
2. THE Docker Compose configuration SHALL remove the `nim-model-cache` volume from `docker-compose.yml`.
3. THE `nemoclaw` service SHALL remove its `depends_on` reference to `nemotron-nim`.
4. THE `nemoclaw` service SHALL include `LLM_API_KEY`, `LLM_API_BASE_URL`, and `LLM_MODEL_NAME` in its environment variables.
5. THE Docker Compose override file SHALL remove the `nemotron-nim` busybox stub service.

### Requirement 5: Update Environment Variable Configuration

**User Story:** As a backend developer, I want the `.env.example` file to document the new API-related environment variables, so that other developers know how to configure the hosted LLM backend.

#### Acceptance Criteria

1. THE `.env.example` file SHALL include `LLM_API_KEY` with a placeholder value and a descriptive comment.
2. THE `.env.example` file SHALL include `LLM_API_BASE_URL` with the default NVIDIA NIM API URL and a descriptive comment.
3. THE `.env.example` file SHALL include `LLM_MODEL_NAME` with the default model name and a descriptive comment.
4. THE `.env.example` file SHALL remove or comment out the `NEMOTRON_NIM_HOST` and `NEMOTRON_NIM_PORT` variables.
5. THE `.env.example` file SHALL retain the `USE_OLLAMA`, `OLLAMA_HOST`, `OLLAMA_PORT`, and `OLLAMA_MODEL` variables for fallback configuration.

### Requirement 6: Update Agent Configuration File

**User Story:** As a backend developer, I want the agent configuration YAML to reflect the new hosted API endpoint and model, so that the configuration stays consistent with the runtime behavior.

#### Acceptance Criteria

1. THE Agent_Config SHALL update the `model` field to reference the `LLM_MODEL_NAME` default value.
2. THE Agent_Config SHALL update the `nim_endpoint` field to reference the `LLM_API_BASE_URL` default value.
3. THE Agent_Config SHALL retain all existing tool definitions, intent mappings, and sandbox configuration unchanged.

### Requirement 7: Unified Inference Path for Classification and Synthesis

**User Story:** As a backend developer, I want both intent classification and response synthesis to use the same hosted API as their primary LLM, so that the inference path is consistent and the two-step Nemotron-then-Ollama synthesis pattern is simplified.

#### Acceptance Criteria

1. THE `classify_intent` function SHALL call the LLM_Client (hosted API) as its primary inference backend.
2. WHEN the LLM_Client fails during classification, THE `classify_intent` function SHALL fall back to Ollama if `USE_OLLAMA` is `true`.
3. THE `synthesize_response` function SHALL call the LLM_Client (hosted API) as its primary inference backend for the analysis step.
4. THE `synthesize_response` function SHALL call Ollama for the summarization step when Ollama is available, preserving the two-step synthesis pattern.
5. WHEN Ollama is not available for summarization, THE `synthesize_response` function SHALL use the LLM_Client for both analysis and summarization.

### Requirement 8: Request Timeout and Error Handling

**User Story:** As a backend developer, I want configurable timeouts and clear error logging for API calls, so that the agent does not hang on slow API responses and failures are diagnosable.

#### Acceptance Criteria

1. THE LLM_Client SHALL enforce a configurable request timeout, read from the `LLM_TIMEOUT_SECONDS` environment variable.
2. WHEN `LLM_TIMEOUT_SECONDS` is not set, THE LLM_Client SHALL default to 30 seconds.
3. WHEN the API_Provider returns an HTTP 401 or 403 status, THE LLM_Client SHALL log an error message indicating an authentication failure.
4. WHEN the API_Provider returns an HTTP 429 status, THE LLM_Client SHALL log a warning message indicating rate limiting.
5. WHEN any request to the API_Provider fails, THE LLM_Client SHALL log the HTTP status code and response body (truncated to 200 characters).

### Requirement 9: Backward Compatibility of Agent API

**User Story:** As a middleware developer, I want the NemoClaw agent's HTTP API to remain unchanged, so that the middleware and iOS app continue to work without modification.

#### Acceptance Criteria

1. THE NemoClaw_Agent SHALL expose the same `/v1/agent` POST endpoint with the same `AgentRequest` and `AgentResponseModel` schemas.
2. THE NemoClaw_Agent SHALL expose the same `/v1/cold_query`, `/v1/hot_query`, `/v1/collisions`, `/v1/accessibility`, `/v1/heat`, `/v1/cultural`, `/v1/edge_cases`, and `/v1/situation_report` endpoints with unchanged request and response formats.
3. THE NemoClaw_Agent SHALL continue to return `requires_clarification: true` when intent confidence is below the `NEMOCLAW_CONFIDENCE_THRESHOLD`.
4. THE NemoClaw_Agent SHALL continue to strip `<think>...</think>` reasoning blocks from LLM responses before returning them to callers.

### Requirement 10: Existing Tests Pass with New Configuration

**User Story:** As a backend developer, I want the existing test suite to pass after the reconfiguration, so that I have confidence the refactoring did not break existing behavior.

#### Acceptance Criteria

1. WHEN the test suite is executed, THE existing tests in `test_agent.py` SHALL pass without modification to test logic.
2. THE test suite SHALL mock the LLM_Client the same way it currently mocks `call_nemotron`, ensuring tests do not make real API calls.
3. THE property-based test for confidence threshold behavior SHALL continue to validate that confidence below 0.7 requires clarification.
