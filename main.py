import asyncio
import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

# Optional: load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Browser Use imports
try:
    from browser_use import Agent, ChatOpenAI
except Exception as e:
    Agent = None  # type: ignore
    ChatOpenAI = None  # type: ignore

# Supabase (uploads for frames)
try:
    from supabase import create_client, Client
except Exception:
    create_client = None  # type: ignore
    Client = None  # type: ignore

# Optional image generation for placeholder frames
try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore


class StartJobRequest(BaseModel):
    task: str = Field(..., description="High-level task for the agent to execute")
    domains: Optional[List[str]] = Field(
        default=None, description="Optional allowlist of domains the agent should focus on"
    )
    max_steps: Optional[int] = Field(default=20, description="Max agent reasoning steps")
    model: Optional[str] = Field(default=os.getenv("BROWSER_USE_MODEL", "gpt-4.1-mini"))


class JobStatusResponse(BaseModel):
    id: str
    status: str
    started_at: str
    completed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


class Job:
    def __init__(self, job_id: str, req: StartJobRequest):
        self.id = job_id
        self.req = req
        self.status: str = "pending"
        self.started_at: str = datetime.utcnow().isoformat()
        self.completed_at: Optional[str] = None
        self.result: Optional[Dict[str, Any]] = None
        self.events_queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        self.cancel_event = asyncio.Event()
        self.supabase: Optional[Client] = init_supabase()


class JobManager:
    def __init__(self):
        self.jobs: Dict[str, Job] = {}

    def create(self, req: StartJobRequest) -> Job:
        job_id = str(uuid.uuid4())
        job = Job(job_id, req)
        self.jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        return job


jobs = JobManager()

app = FastAPI(title="Browser Agent Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def emit(job: Job, type_: str, data: Dict[str, Any]) -> None:
    await job.events_queue.put({"type": type_, **data, "ts": datetime.utcnow().isoformat()})


async def run_agent_job(job: Job) -> None:
    job.status = "running"
    await emit(job, "browser_started", {"jobId": job.id, "task": job.req.task})

    if Agent is None or ChatOpenAI is None:
        await emit(job, "browser_error", {"message": "browser_use not installed"})
        job.status = "error"
        job.completed_at = datetime.utcnow().isoformat()
        job.result = {"summary": "browser_use not installed on server"}
        await emit(job, "browser_done", {"result": job.result})
        return

    try:
        llm = ChatOpenAI(model=job.req.model)
        agent = Agent(task=job.req.task, llm=llm)

        # Heartbeat task to emit progress pings
        async def heartbeat() -> None:
            step = 0
            try:
                while job.status == "running" and not job.cancel_event.is_set():
                    step += 1
                    await emit(job, "browser_action", {"text": f"Working... step {step}"})
                    # Emit a placeholder frame so the UI integration can be validated
                    try:
                        frame_bytes = generate_placeholder_frame_bytes(f"Step {step}")
                        if frame_bytes is not None:
                            url = await upload_frame(job, frame_bytes, f"step_{step}.png")
                            if url:
                                await emit(job, "browser_frame", {"url": url})
                    except Exception:
                        pass
                    await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                return

        hb = asyncio.create_task(heartbeat())

        # Execute agent
        result_text = await agent.run()

        hb.cancel()
        try:
            await hb
        except Exception:
            pass

        # Basic link extraction from result text
        links = re.findall(r"https?://[^\s)]+", result_text or "")
        job.result = {"summary": result_text, "links": links}
        # Emit a final placeholder summary frame
        try:
            frame_bytes = generate_placeholder_frame_bytes("Completed")
            if frame_bytes is not None:
                url = await upload_frame(job, frame_bytes, "completed.png")
                if url:
                    await emit(job, "browser_frame", {"url": url})
        except Exception:
            pass
        job.status = "completed"
        job.completed_at = datetime.utcnow().isoformat()
        await emit(job, "browser_done", {"result": job.result})
    except Exception as e:
        job.status = "error"
        job.completed_at = datetime.utcnow().isoformat()
        msg = str(e)
        job.result = {"summary": f"Error: {msg}"}
        await emit(job, "browser_error", {"message": msg})
        await emit(job, "browser_done", {"result": job.result})


@app.post("/jobs", response_model=JobStatusResponse)
async def start_job(req: StartJobRequest):
    job = jobs.create(req)
    asyncio.create_task(run_agent_job(job))
    return JobStatusResponse(
        id=job.id,
        status=job.status,
        started_at=job.started_at,
        completed_at=job.completed_at,
        result=job.result,
    )


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    try:
        job = jobs.get(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(
        id=job.id,
        status=job.status,
        started_at=job.started_at,
        completed_at=job.completed_at,
        result=job.result,
    )


async def event_generator(job: Job) -> AsyncGenerator[str, None]:
    try:
        while True:
            event = await job.events_queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") == "browser_done":
                break
    except asyncio.CancelledError:
        return


@app.get("/jobs/{job_id}/events")
async def stream_job_events(job_id: str):
    try:
        job = jobs.get(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")
    return EventSourceResponse(event_generator(job))


@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    try:
        job = jobs.get(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")
    job.cancel_event.set()
    job.status = "cancelled"
    job.completed_at = datetime.utcnow().isoformat()
    await emit(job, "browser_error", {"message": "Cancelled by user"})
    await emit(job, "browser_done", {"result": job.result or {"summary": "Cancelled"}})
    return {"ok": True}


@app.get("/")
async def root():
    return {"service": "browser-agent", "status": "ok"}


# --- Helpers: Supabase and frames ---

def init_supabase() -> Optional[Client]:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        return None
    if create_client is None:
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None


async def upload_frame(job: Job, image_bytes: bytes, name: str) -> Optional[str]:
    if not job.supabase:
        return None
    bucket = os.getenv("SUPABASE_BUCKET", "browser-frames")
    job_dir = f"jobs/{job.id}"
    path = f"{job_dir}/{name}"
    try:
        # Ensure bucket exists (best-effort)
        try:
            job.supabase.storage.get_bucket(bucket)
        except Exception:
            try:
                job.supabase.storage.create_bucket(bucket)
            except Exception:
                pass

        job.supabase.storage.from_(bucket).upload(path, image_bytes, {
            "content-type": "image/png",
            "upsert": True
        })

        # Create signed URL for 15 minutes
        signed = job.supabase.storage.from_(bucket).create_signed_url(path, 900)
        return signed.get("signedURL") or signed.get("signed_url")
    except Exception:
        return None


def generate_placeholder_frame_bytes(text: str) -> Optional[bytes]:
    if Image is None:
        return None
    try:
        img = Image.new("RGB", (900, 560), color=(18, 18, 18))
        draw = ImageDraw.Draw(img)
        title = "Professor Lock Browser"
        subtitle = f"{text}"
        color = (0, 200, 255)
        white = (240, 240, 240)
        # Fallback fonts
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()
        draw.text((30, 30), title, fill=color, font=font_title)
        draw.text((30, 80), subtitle, fill=white, font=font_sub)
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


