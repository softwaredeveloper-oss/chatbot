# TCI Express AI Chatbot

A Flask chatbot that answers questions about **TCI Express**. It gets its information live from the
official TCI Express website, the TCI Express shipment-tracking page and the web (Tavily search), and
uses an LLM (Groq) only to phrase the answer. For booking / docket / branch / customer / pincode data it can
also query your **Oracle database** (read-only). There is no vector store.

## What it can answer

| Question | Where the answer comes from |
|---|---|
| A website link in the message | The page is fetched and summarised |
| Shipment tracking (a DWB / docket number, "track ...") | Live tracking page on tciexpress.in |
| Board of directors and committees | Official board page (shown as tables, no LLM) |
| Senior people (Chairman, MD, CFO, directors, anyone on the official Top Management or Board pages) | **Only** the official website pages, current office-holders first |
| Official-site topics (contact details, about/mission, each express service, value added services, infrastructure, quality policy) | The matching official page |
| A person's name (e.g. "Mohit") | Table of public LinkedIn profiles found by web search |
| Booking data: dockets, bookings, freight, customers, branches, pincodes (e.g. "top 5 customers by freight", "branches in Gujarat", "details of docket 041294971") | **Oracle database**, read-only text-to-SQL (see below) |
| Revenue, results, news, share price, other questions | Web search (Tavily): the official site first, then the wider web |

The chatbot only discusses TCI Express. Other questions get a fixed refusal.

## How a message is handled (`/get`)

1. Input checks: empty message, 800-character limit, short follow-ups reuse the previous question.
2. A URL in the message: scrape and summarise it.
3. A DWB number with a tracking word: live tracking.
4. Board / committee question: official board page.
5. Senior-people question: official Top Management and Board pages.
6. Booking-data question: Oracle database (only when `db_config.yaml` is filled in; skipped for official-site questions).
   If the database cannot answer a vague question, the flow continues below.
7. Person name: LinkedIn profile table (skipped when an official page answers the question).
8. "Revenue of TCI" type questions: web answer.
9. Everything else: web search, official site first; if nothing is found the fallback message is shown.

The routing is regex based, so no LLM call is made just to decide where a question goes.

## Project layout

```
app.py                  Flask app, /get route and the routing order above
web_search.py           Tavily web search, LinkedIn name lookup, search guardrails
src/
  oracle_service.py     Oracle text-to-SQL: LLM writes one SELECT, validator, read-only session
  site_service.py       Reads official tciexpress.in pages (cached 15 min), senior-people lookup
  board_service.py      Board and committee tables from the official page (cached 1 h)
  tracking_service.py   Live shipment tracking page parser
  router.py             Regex helpers (name and finance question detection)
  helper.py             URL detection and website scraping
  prompt.py             Prompt for the website-summary feature
templates/chat.html     The chat page (single page)
db_config.yaml          Oracle credentials + allowed tables (git-ignored; template: db_config.example.yaml)
requirements.txt        Python packages
context.md              Deployment notes and session history
guardrail.md            Every guardrail the chatbot applies, and known gaps
PERF_BASELINE.md        Startup / latency measurements
Here are just the Nginx steps, done.txt   Server nginx setup
_archive_unused/        Files that are no longer used (can be deleted or restored)
```

## Setup

Requires **Python 3.12 or newer**.

```bash
python -m venv venv
venv\Scripts\activate            # Windows   (Linux: source venv/bin/activate)
pip install -r requirements.txt
```

Create a `.env` file in the project root (never commit it):

```ini
GROQ_API_KEY=...        # required - the LLM (Groq)
TAVILY_API_KEY=...      # required - web search
FLASK_SECRET_KEY=...    # recommended - keeps the "last question" session valid across restarts
APP_HOST=0.0.0.0        # optional (use 127.0.0.1 behind nginx)
APP_PORT=8000           # optional
```

The app stops at startup with a clear error if `GROQ_API_KEY` or `TAVILY_API_KEY` is missing.

## Oracle database (optional)

1. Copy `db_config.example.yaml` to `db_config.yaml` and fill in the user, password, DSN and your tables
   (`db_config.yaml` is git-ignored: it holds the password). Restart the app. If the file is missing or not filled
   in, the feature is off and everything else works.
2. Works with Oracle 12c or newer out of the box ("Thin" mode, nothing to install). For an old 11g server
   set `client_lib_dir` to an unpacked Oracle Instant Client 19c folder.
3. The server running the chatbot must be able to reach the database port (usually 1521).

**It is read-only and restricted, in layers:** only one `SELECT` is accepted (no inserts/updates/deletes, comments,
quoted names, DB links, dictionary views or unknown functions); only the tables listed in `db_config.yaml`; columns
listed under `hidden_columns` can never be used or returned; every query runs in a read-only database
transaction with a row cap (`max_rows`) and a timeout; the database account is used only for these queries.
Use a dedicated read-only Oracle user if you can: the app enforces read-only itself, but least privilege is safer.

The LLM only sees the tables relevant to the question, in compact form, with each table's data period
(the data may not be live, so "last month" is read relative to the newest date). Tune it by improving the
descriptions, `keywords`, `joins` and `examples` in `db_config.yaml`.

## Run

```bash
python app.py
```

Then open <http://localhost:8000>. Startup takes a few seconds (no data files or models are loaded).

## Deployment

The server runs the app as a systemd service (`chatbot.service`) behind nginx. See `context.md` and the
nginx notes file. If nginx sits in front, set `client_max_body_size` and `proxy_read_timeout 180s` so slow
answers do not return a 504.

## Troubleshooting

| Message / symptom | Cause and fix |
|---|---|
| "The web search service has reached its usage limit..." | The Tavily plan limit is used up. Wait for the monthly reset, upgrade the plan, or put a new key in `.env` and restart. |
| "The AI service has reached its usage limit..." | The Groq daily or per-minute token limit was hit. Wait, or use a higher Groq tier. |
| "I couldn't reach the database right now" | Oracle is down, the network/port is blocked, or the login in `db_config.yaml` is wrong (see the server log). |
| `Missing required env variables` | `.env` is missing or incomplete (see Setup). |
| "Sorry, I couldn't find relevant information on the web" | The web search returned no TCI Express results for that question. Rephrase it. |

## Things to know

- Web answers depend on Tavily and Groq, which have usage limits; people questions use several searches each.
- LinkedIn details come only from public search snippets and can be incomplete or outdated; senior people
  come from the official website instead.
- There is no login or rate limiting on `/get`. See `guardrail.md` for the known gaps (SSRF on URL scraping,
  prompt injection, rate limiting).
