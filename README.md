# Browser Agent Service

A lightweight FastAPI microservice that runs a Browser Use agent in a real Chromium instance, emitting live events (SSE) and returning a final summary + links. Designed to be called by the Node orchestrator as a function tool.

## Endpoints
- POST `/jobs` start a new browsing job
- GET `/jobs/{id}` poll job status/result
- GET `/jobs/{id}/events` stream live events via Server-Sent Events
- POST `/jobs/{id}/cancel` cancel a running job

## Quickstart
1. Python 3.11+
2. Install deps:
   ```bash
   uv pip install -r requirements.txt
   # If Chromium not installed
   uvx playwright install chromium --with-deps --no-shell
   ```
3. Run service:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8091
   ```
4. Configure backend env:
   ```bash
   export BROWSER_AGENT_URL=http://localhost:8091
   ```

## Notes
- Uses `browser-use` `Agent(task=..., llm=ChatOpenAI(...))`. Set model via env `BROWSER_USE_MODEL` or request body.
- Events emitted:
  - `browser_started`, `browser_action`, `browser_done`, `browser_error`
- Extend to send screenshots: capture frames and emit `browser_frame` events with pre-signed URLs to Supabase Storage if desired.

## References
- Browser Use: [github.com/browser-use/browser-use](https://github.com/browser-use/browser-use)

# browserpplay
