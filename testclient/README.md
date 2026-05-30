# testclient/ — throwaway test frontend

**Test-only. Not part of the product or the reusable core.**

`index.html` is a single static page (no build step, no framework) used
purely to talk to the local dev server during development. Nothing in
`core/` depends on anything here. A real product ships its own client.

## Use it

1. Set up `.env` with `GROQ_API_KEY` and `DEEPGRAM_API_KEY`.
2. Start the server: `python -m server`
3. Open <http://localhost:8000/> (the server serves this folder at `/`).
4. Click **Talk**, allow the microphone, and speak. Click **Hang up** to end.

Use `http://localhost` (or HTTPS) — browsers only allow microphone access
on secure origins / localhost.
