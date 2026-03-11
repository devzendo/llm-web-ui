# llm-web-ui

A responsive web frontend for Simon Willison's [`llm`](https://llm.datasette.io/) library.
Create new chats, revisit old ones, and stream responses from any local or remote model
`llm` supports — all in a clean dark UI built with Framework7.

_Note by Matt - I HAVE NOT YET VERIFIED THIS. ALL CODE WRITTEN BY CLAUDE
AND NOT YET EXAMINED FOR CORRECTNESS. BUYER BEWARE. MAY CONTAIN NUTS._
---

## Quick start

```bash
# 1. Install dependencies
pip install llm fastapi uvicorn

# 2. Install a local model plugin (e.g. Ollama)
pip install llm-ollama
ollama pull llama3.2          # or any model you like

# 3. Run
uvicorn app:app --port 8000 --reload

# 4. Open http://localhost:8000
```

Both `app.py` and `index.html` must be in the **same directory**.

---

## Features

| Feature | Detail |
|---|---|
| **Streaming** | Tokens appear word-by-word via Server-Sent Events |
| **Conversation history** | Sidebar lists all past chats from llm's SQLite DB |
| **Resume chats** | Click any past conversation to reload and continue it |
| **Delete chats** | Hover a conversation → click ✕ |
| **Model switching** | Dropdown shows every model `llm` has installed |
| **System prompt** | Collapsible field, applied to new conversations |
| **Markdown rendering** | AI responses rendered with `marked.js` |
| **Responsive** | Works on desktop and mobile (Framework7 panel swipe) |

---

## How it accesses the SQLite database

`llm` stores all conversations in a local SQLite file. The server finds it by running:

```bash
llm logs path
# → /Users/you/Library/Application Support/io.datasette.llm/logs.db  (macOS)
# → /home/you/.config/io.datasette.llm/logs.db                        (Linux)
```

The database is fully open — no authentication required. The schema is:

```sql
CREATE TABLE conversations (id TEXT PRIMARY KEY, name TEXT, model TEXT);

CREATE TABLE responses (
    id TEXT PRIMARY KEY, model TEXT, prompt TEXT, system TEXT,
    response TEXT, conversation_id TEXT REFERENCES conversations(id),
    duration_ms INTEGER, datetime_utc TEXT,
    input_tokens INTEGER, output_tokens INTEGER
);
```

The web server reads these tables directly with Python's built-in `sqlite3`. Writes happen
through the `llm` Python API, which logs everything automatically.

---

## Dependencies

**Python (pip):**
```
llm          # Simon Willison's LLM library
fastapi      # Web framework
uvicorn      # ASGI server
```
Plus any `llm` model plugins you want (e.g. `llm-ollama`, `llm-gpt4all`, `llm-anthropic`).

**Frontend (CDN, no npm/build step):**
- [Framework7 v8](https://framework7.io/) — UI components & responsive layout
- [marked.js v12](https://marked.js.org/) — Markdown rendering

That's it. No Node.js. No bundler. No build step.

---

## Environment variable for custom DB path

If you keep your `llm` data in a non-default location, set:

```bash
export LLM_USER_PATH=/your/custom/path
uvicorn app:app --port 8000
```

The server calls `llm logs path` at runtime, which respects this variable.

---

## Production tips

- Run behind `nginx` with a reverse proxy to port 8000
- Add HTTP Basic Auth in nginx if exposing on a LAN
- Use `uvicorn app:app --workers 1` (single worker keeps the in-memory conversation
  store consistent — multiple workers would lose conversation context between requests)
