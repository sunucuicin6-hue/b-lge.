import json
import os
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from research_engine import run_research

app = FastAPI(title="Derin Araştırma API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # geliştirme için; canlıya alırken kendi domain'inle sınırla
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResearchRequest(BaseModel):
    topic: str


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/research")
def research(req: ResearchRequest):
    def stream():
        for event in run_research(req.topic):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
