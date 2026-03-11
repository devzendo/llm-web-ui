"""
llm-web-ui: A web frontend for Simon Willison's `llm` library.

Usage:
    pip install llm fastapi uvicorn llm-ollama   # + any other llm plugins
    uvicorn app:app --reload --port 8000
"""

import asyncio
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import AsyncGenerator, Optional

import llm
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="llm-web-ui")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_db_path() -> Path:
    """Return the path to llm's logs.db, using the llm CLI so it respects
    any LLM_USER_PATH environment variable the user may have set."""
    result = subprocess.run(
        ["llm", "logs", "path"], capture_output=True, text=True
    )
    return Path(result.stdout.strip())


def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# In-memory conversation store: maps conversation_id -> llm Conversation obj
# We keep live objects here so streaming continues across requests.
# ---------------------------------------------------------------------------
_conversations: dict[str, object] = {}


# ---------------------------------------------------------------------------
# API Models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None  # None = new conversation
    model_id: str
    message: str
    system: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes: conversations / history
# ---------------------------------------------------------------------------

@app.get("/api/conversations")
def list_conversations():
    """Return all conversations ordered by most recent response."""
    with db_conn() as conn:
        rows = conn.execute("""
            SELECT
                c.id,
                c.name,
                c.model,
                MAX(r.datetime_utc) AS last_active,
                COUNT(r.id) AS message_count
            FROM conversations c
            LEFT JOIN responses r ON r.conversation_id = c.id
            GROUP BY c.id
            ORDER BY last_active DESC
        """).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    """Return all messages for a conversation."""
    with db_conn() as conn:
        meta = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if not meta:
            raise HTTPException(status_code=404, detail="Conversation not found")
        messages = conn.execute(
            """SELECT id, model, prompt, response, datetime_utc, duration_ms,
                      input_tokens, output_tokens
               FROM responses
               WHERE conversation_id = ?
               ORDER BY datetime_utc ASC""",
            (conversation_id,),
        ).fetchall()
    return {
        "conversation": dict(meta),
        "messages": [dict(m) for m in messages],
    }


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    """Delete a conversation and all its responses."""
    with db_conn() as conn:
        conn.execute(
            "DELETE FROM responses WHERE conversation_id = ?", (conversation_id,)
        )
        conn.execute(
            "DELETE FROM conversations WHERE id = ?", (conversation_id,)
        )
        conn.commit()
    _conversations.pop(conversation_id, None)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Routes: models
# ---------------------------------------------------------------------------

@app.get("/api/models")
def list_models():
    models = []
    for m in llm.get_models():
        models.append({
            "id": m.model_id,
            "name": getattr(m, "name", m.model_id),
            "aliases": list(getattr(m, "aliases", [])),
        })
    return models


# ---------------------------------------------------------------------------
# Route: streaming chat
# ---------------------------------------------------------------------------

@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    """
    POST a message; returns a text/event-stream of SSE chunks.

    SSE events:
        data: {"type": "token", "text": "..."}
        data: {"type": "meta",  "conversation_id": "...", "response_id": "..."}
        data: {"type": "done"}
        data: {"type": "error", "message": "..."}
    """

    async def generate() -> AsyncGenerator[str, None]:
        try:
            model = llm.get_model(req.model_id)
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            return

        # Reconnect to existing conversation or start fresh
        conv_id = req.conversation_id
        if conv_id and conv_id in _conversations:
            conversation = _conversations[conv_id]
        else:
            # Build a new conversation object
            kwargs = {}
            if req.system:
                kwargs["system"] = req.system
            conversation = model.conversation(**kwargs)

        # Run the blocking llm call in a thread so we don't block the event loop
        loop = asyncio.get_event_loop()

        collected_tokens: list[str] = []

        def run_prompt():
            response = conversation.prompt(req.message)
            for chunk in response:
                collected_tokens.append(chunk)
            return response

        response = await loop.run_in_executor(None, run_prompt)

        # Stream tokens we already collected (llm buffers internally)
        # For true streaming we iterate inline — see streaming variant below.
        # Here we use the simpler approach: yield all collected tokens.
        new_conv_id = None
        try:
            new_conv_id = conversation.responses[-1].conversation_id if conversation.responses else conv_id
        except Exception:
            pass

        for token in collected_tokens:
            yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"
            await asyncio.sleep(0)  # yield control

        # Persist conversation object for future turns
        if new_conv_id:
            _conversations[new_conv_id] = conversation

        # Attempt to get the logged response id from the DB for reference
        response_id = None
        try:
            with db_conn() as conn:
                row = conn.execute(
                    "SELECT id FROM responses WHERE conversation_id = ? ORDER BY datetime_utc DESC LIMIT 1",
                    (new_conv_id,),
                ).fetchone()
                if row:
                    response_id = row["id"]
        except Exception:
            pass

        yield f"data: {json.dumps({'type': 'meta', 'conversation_id': new_conv_id, 'response_id': response_id})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Serve the single-page frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(content=html_path.read_text())


# ---------------------------------------------------------------------------
# Dev entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
