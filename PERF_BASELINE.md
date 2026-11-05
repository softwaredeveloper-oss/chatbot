# Performance baseline (before any optimization)

Measured 2026-11-05 on the Windows PC, `venv313` (Python 3.13), checkpoint commit `3175b86`.
Measurement scripts are kept outside the project (not in git). App state = web-only
(Excel analytics and Pinecone/RAG were already removed before this baseline).

## Startup (cold process, `python app.py` until `GET /` returns 200)

| Metric | Run 1 | Run 2 |
|---|---|---|
| Startup time | **7.8 s** | 6.1 s |
| Memory (RSS) after startup | **331 MB** | 330 MB |
| Memory after the question set | 342 MB | 334 MB |

`python -X importtime -c "import app"` (cold imports only): **~12.6 s** with `torch`/`transformers` importable,
of which `transformers` alone is ~7.3 s and `torch` ~3.3 s. They are imported by
`langchain_core` (token-counting helper) simply because both packages are still installed in `venv313`.
The app itself never uses them any more.

Estimate of the same import with `transformers`/`torch` blocked (what a venv built from the current
`requirements.txt` would see): import time **~2.0 s** (was ~6.7 s on a warm cache), RSS **~104 MB** (was ~322 MB).
Remaining import cost: `langchain_openai`/`openai` ~0.65 s, `langchain_text_splitters` ~0.4 s (only used by dead code).

## Per-question latency (end-to-end `POST /get`, run 1 - all services healthy)

| Question type | Sample question | Latency | Source |
|---|---|---|---|
| Tracking (DWB) | `track 299106861` | 1.6 s | TCI live tracking (HTTP fetch + 1 LLM call) |
| Board, first call | `board of directors of TCI Express` | 0.3 s | official page (no LLM) |
| Board, cached (1 h cache) | same | 0.0 s | cache |
| Web search (first web call after start) | `What services does TCI Express offer?` | 12.4 s | Tavily + 1 LLM call (first call also pays connection/TLS warm-up) |
| Web search (news) | `latest news about TCI Express` | 1.5 s | Tavily (2 searches) + LLM |
| Finance | `revenue of tci` | 1.1 s | Tavily + LLM |
| Person name | `Mohit` | 1.6 s | 1 + 10 Tavily LinkedIn searches (8 in parallel), no LLM |
| People list | `who are the developers in TCI Express` | **21.6 s** | up to 7 **sequential** `advanced` Tavily searches + LLM |
| Off-topic | `capital of France` | 0.6 s | Tavily, no TCI results, fallback text |

RAG and Excel analytics questions no longer exist in the app, so they cannot be measured.

### Caveat - Tavily quota
Run 2 returned the fallback message for every web question because the Tavily plan limit was reached
(`This request exceeds your plan's set usage limit`). Run 2 web timings are therefore invalid and only
run 1 is used as the baseline. Re-running the web questions for the "after" numbers needs Tavily credits.
Note: the app swallows that error and shows the generic "couldn't find relevant information" message.

## Where the time goes

1. **Startup**: ~7 s of import time is `transformers` + `torch` that the app does not use. No data file or
   model is loaded any more.
2. **Per request**: no LLM call is used to classify or route; routing is regex only. Cost is dominated by
   Tavily calls (sequential) and the single answer LLM call.
   - People questions run up to 7 `search_depth="advanced"` searches one after another.
   - A finance question that gets the refusal answer runs the web search a second time in the fallback step.
   - The name-lookup does one search, then 10 in parallel (already parallel).
   - Tracking, board (cached) and URL flows are already about as fast as their network allows.
3. `max_tokens=600/900`, `k` and retries are not significant (no RAG, no sleeps/retries in the request path).
