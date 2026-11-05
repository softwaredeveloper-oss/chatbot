# UAT Report – TCI Express AI Chatbot

**Date:** 26-Oct-2026  ·  **Build:** current `AICHATBOT` folder (port 8000, `debug=False`, local embedding model)
**Where tested:** Windows PC, set up like the server – Python 3.12, full `requirements.txt` (torch 2.14.0 CPU), `python app.py`, `HF_HUB_OFFLINE=1`, real `.env` keys, real data (`Jul-2026.xls`, `cm_pincode.xlsx`).
**Not changed during the test:** the Pinecone knowledge base (upload tests stopped at validation, nothing was written).

## Verdict

**Works for one user at a time. Not ready for several users at once** – the Groq free-tier rate limit makes about 2 in 5 answers fail when people chat at the same time (finding #1).

| | Result |
|---|---|
| Startup | ✅ Clean – 441,984 dockets + 26,566 pincodes loaded, model loaded offline, `Debug mode: off`, port 8000 |
| Single-user tests | ✅ 25 / 26 passed (1 low-priority fail) |
| 5 users at once | ❌ 2 / 5 failed (Groq rate limit) |
| Memory | ~950 MB RAM for the app → server needs **2 GB+ free RAM** |
| Response time | 0.3 s – 20 s, typically 4 – 15 s |

## Test results

| ID | Area | Test | Expected | Result | Time |
|---|---|---|---|---|---|
| P1 | Pages | Open `/` (chat) | Page loads | ✅ PASS | 0.0 s |
| P2 | Pages | Open `/knowledge-upload.html` | Page loads | ✅ PASS | 0.0 s |
| P3 | Pages | Open `/record-view.html` | Page loads | ✅ PASS | 0.0 s |
| C1 | Chat | "hello" | Friendly greeting | ✅ PASS – "Hello! How can I help you today?" | 15.6 s |
| C2 | Knowledge | "What is TCI Express?" | Company answer | ✅ PASS – answered from web search | 20.3 s |
| C3 | Knowledge | "What services does TCI Express offer?" | Services answer | ✅ PASS – answered from web search | 4.4 s |
| C4 | Knowledge | "What is the ESG policy of TCI Express?" | ESG answer | ✅ PASS – answered from web search | 5.1 s |
| T1 | Tracking | "track 299106861" | Live tracking | ✅ PASS – Delivered 06-AUG-2026, XDPT ➔ XNRL, 3 movements | 2.5 s |
| T2 | Tracking | "mera shipment 299106861 kaha hai" (Hinglish) | Live tracking | ✅ PASS | 14.5 s |
| T3 | Tracking | "track 114019110612" | Live tracking | ✅ PASS – Delivered 09-JUL-2026, 4 movements | 13.8 s |
| A1 | Analytics | "top 5 booking branches by total freight" | Table | ✅ PASS – XMSI, XBGN, XPNY, XHMI, XSPH | 17.5 s |
| A2 | Analytics | "total freight revenue by DIV" | Table | ✅ PASS – 8 divisions | 3.3 s |
| A3 | Analytics | "details of docket 114019110612" | Docket record | ✅ PASS | 0.3 s |
| A4 | Analytics | "bar chart of total freight by DIV" | Chart | ✅ PASS | 5.9 s |
| A5 | Analytics | Open the generated chart image | PNG loads | ✅ PASS – 34 KB PNG | 0.0 s |
| N1 | Pincode | "pincode 110001 details" | Pincode table | ✅ PASS | 7.8 s |
| N2 | Pincode | "is pincode 400001 serviceable?" | Pincode answer | ✅ PASS | 15.0 s |
| U1 | Website | "summarize https://www.tciexpress.in" | Summary | ✅ PASS | 14.0 s |
| V1 | Validation | Empty message | HTTP 400 | ✅ PASS | 0.0 s |
| V2 | Validation | 801-character message | HTTP 400 | ✅ PASS | 0.0 s |
| V3 | Validation | Upload with no file | HTTP 400 | ✅ PASS | 0.0 s |
| V4 | Validation | Upload a `.txt` file | HTTP 400 | ✅ PASS | 0.0 s |
| V5 | Validation | Add empty knowledge text | HTTP 400 | ✅ PASS | 0.0 s |
| V6 | Validation | Add knowledge text sent as a form (not JSON) | HTTP 400 | ❌ FAIL – returns 500 | 0.0 s |
| V7 | Validation | Unknown page | HTTP 404 | ✅ PASS | 0.0 s |
| L1 | Load | 5 different questions at the same time | All answered | ❌ FAIL – 2 of 5 returned "Something went wrong" | 31.8 s |

## Findings (most important first)

### 1. 🔴 Groq rate limit – answers fail when several people chat at once
- **Seen in:** L1. Server log: `Error code: 429 – Rate limit reached for model qwen/qwen3.8-27b … on output tokens per minute (OTPM): Limit 1000`.
- **Why:** the Groq account is on the free *on_demand* tier – **1,000 output tokens per minute for the whole account**. Each answer can use 400–800 tokens, so only **~2 answers per minute** fit, shared by all users. Only Excel analytics retries; knowledge, web, URL and tracking answers fail straight away with "Something went wrong".
- **Fix:** upgrade the Groq account to Dev Tier (console.groq.com → Settings → Billing). Code can also help: retry on 429 everywhere, show "Busy, please try again in a few seconds" instead of a generic error, and lower `max_tokens`.

### 2. 🟠 Knowledge base (Pinecone) never answered
- **Seen in:** C2, C3, C4 – all three came from **Live Web Search**, not the Vector Database.
- **Why (likely):** `app.py` treats any answer containing **"esg report"** as a refusal (`refusal_signals` list), so ESG answers from your documents are thrown away and replaced with web results. The index may also simply not contain company documents yet.
- **Fix:** remove `"esg report"` from `refusal_signals` in `app.py`, and check which documents are in the `chatbot-1` index.

### 3. 🟠 Web answers start with "Based on the provided web search context…"
- **Seen in:** C2, C3, C4. Users see the bot talking about its own internals.
- **Fix:** add "answer directly, don't mention the context or web search" to the Tavily prompt in `app.py`.

### 4. 🟡 Tracking answer shows raw `*`, `` ` `` and `---` in the chat window
- **Seen in:** T1–T3. The tracking reply uses `* ` bullets, backticks and `---`, but the chat page only understands `- ` bullets and `**bold**`.
- **Also:** Destination and Package Details show "N/A", and the booking table the server builds has wrong values (every row = booking station). The chat page doesn't display that table, so users only see the N/A lines.
- **Fix:** teach `formatBotMessage` in `chat.html` to handle `* `, `` ` `` and `---`; fix booking parsing in `src/tracking_service.py`.

### 5. 🟡 Pincode answers are titled "DWB Details (1 record mila)"
- **Seen in:** N1, N2. Any one-row table gets the docket title.
- **Fix:** use a neutral title ("1 record mila") in `src/analytics_engine.py`.

### 6. 🟡 Upload page promises file types the server rejects
- **Seen in:** V4. The page says "Supported: PDF, XLS, XLSX, CSV, TXT and DOCX", but the server only accepts PDF, XLS, XLSX.
- **Fix:** change the text and `accept=` list in `knowledge-upload.html` to PDF, XLS, XLSX.

### 7. ⚪ Wrong error code for bad text-upload requests
- **Seen in:** V6. Returns 500 instead of 400. The upload page always sends JSON, so normal users never hit this.

### Harmless warnings in the startup log
- `Could not determine dtype for column …` (pincode file) – falls back to text, works fine.
- `Parameters {'reasoning_effort'} should be specified explicitly` – works fine.
- `HuggingFaceEmbeddings was deprecated` – works fine.
- `This is a development server` – fine for now; use gunicorn later for more users.

## Not covered by this UAT

- **The Linux server itself** – firewall access to Groq / Pinecone / Tavily / tciexpress.in, and port 8000 from outside. Run Steps 1–10 in `context.md`.
- **Uploading files/text into Pinecone** – skipped on purpose so the live knowledge base wasn't changed. Test once with a small file on the server.
- **How pages look in a browser** – the tests checked the server's replies, not the screen. Use the checklist below.

## Browser checklist (run on http://124.7.209.74:8000 after deploying)

| # | Do this | Expected | OK? |
|---|---|---|---|
| 1 | Open http://124.7.209.74:8000 | Chat window with TCI Express logo | ☐ |
| 2 | Type `hello` | Greeting, with a source badge | ☐ |
| 3 | Type `track 299106861` | Delivered status + timeline | ☐ |
| 4 | Type `top 5 booking branches by total freight` | Table of 5 branches | ☐ |
| 5 | Type `bar chart of total freight by DIV` | Chart image; click it → opens full size | ☐ |
| 6 | Type `details of docket 114019110612`, click **View Full Record** | New tab with the record table, search and Print work | ☐ |
| 7 | Type `pincode 110001 details` | Pincode table | ☐ |
| 8 | Type `summarize https://www.tciexpress.in` | Website summary + "URL Analyzed" badge | ☐ |
| 9 | Click **Add Knowledge** → upload a small PDF | Progress bar → "Saved to Vector Database" | ☐ |
| 10 | Try the minimize / standard / full-screen buttons | Window resizes correctly | ☐ |
| 11 | Open the site on a phone | Chat fits the screen | ☐ |
