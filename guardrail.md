# Web Guardrails – TCI Express AI Chatbot

Every guardrail that controls what the chatbot fetches from, searches on, or says about the web.
Compiled from `app.py`, `web_search.py`, `src/router.py`, `src/helper.py`, `src/board_service.py`, `src/prompt.py`.

Legend: ✅ enforced in code · 📝 enforced only by prompt (the LLM may ignore it) · ⚠️ gap / weakness

---

## 1. Input guardrails (`app.py` `/get`)

| # | Guardrail | Where | Type |
|---|---|---|---|
| 1.1 | Empty message → HTTP 400 | `app.py` chat() | ✅ |
| 1.2 | Max message length 800 chars → HTTP 400 | `MAX_MSG_LENGTH` | ✅ |
| 1.3 | Follow-up handling: short messages ("more", "linkedin", "she is in HR") reuse the previous question stored in the session; skipped if the message mentions "tci" | `session["last_q"]` | ✅ |
| 1.4 | "Search from internet" cue (`INTERNET_CUE`) forces web search and strips the phrase from the query | `app.py` | ✅ |
| 1.5 | Upload accepts only `.pdf .xls .xlsx`; others → 400 | `/api/knowledge/pdf` | ✅ |
| 1.6 | Upload temp file is deleted in `finally` | `/api/knowledge/pdf` | ✅ |

## 2. Web search scope guardrails (`web_search.py`)

| # | Guardrail | Detail | Type |
|---|---|---|---|
| 2.1 | **TCI-only results** | `is_about_tci()` keeps a result only if title/URL/content matches `TCI_PATTERN` (`tci express`, `tciexpress`, `tciexp`). Applied to both people and general searches | ✅ |
| 2.2 | **Query is prefixed** with `TCI Express` so searches stay on-topic | `search_tciexpress()` | ✅ |
| 2.3 | **Freshness: last 12 months by default** (`time_range="year"`, news `days=365`) | `search_tciexpress()` | ✅ |
| 2.4 | Older data only when asked: `wants_older_info()` (history, former, since, years before current year, FY before current) | `OLDER_INFO_PATTERN` | ✅ |
| 2.5 | Date filter skipped for contact/address/branch questions (pages have no publish date) | `STATIC_INFO_PATTERN` | ✅ |
| 2.6 | People questions: search restricted to `linkedin.com` (plus one general query); no date filter | `include_domains=["linkedin.com"]` | ✅ |
| 2.7 | Result caps: 3 results to the LLM for normal questions, 12 for people, 10 per LinkedIn query, 60 rows in a name table | `web_answer_for`, `search_tciexpress`, `chat()` | ✅ |
| 2.8 | Tavily config: `include_answer=False`, `include_raw_content=False` (no third-party generated answer, no full pages) | `search()` | ✅ |
| 2.9 | Search errors are swallowed → empty list → fallback message (no stack trace to user) | `search()` | ✅ |
| 2.10 | Empty/blank query returns `[]` without calling the API | `search()` | ✅ |

## 3. Person / LinkedIn guardrails

| # | Guardrail | Where | Type |
|---|---|---|---|
| 3.1 | Profile must be a `linkedin.com/in/<slug>` URL | `_parse_profile` | ✅ |
| 3.2 | **Every searched name token must be a whole word in the profile's name** (no partial matches) | `_parse_profile` | ✅ |
| 3.3 | Profile must mention TCI / TCI Express / Transport Corporation of India | `PROFILE_TCI_PATTERN` | ✅ |
| 3.4 | **Other TCI Group companies excluded** (Supply Chain, Freight, Global, Seaways, Logistics, Cold Chain, Foundation, Developers, Infrastructure) unless TCI Express/XPS is also named | `OTHER_TCI_PATTERN` | ✅ |
| 3.5 | Profiles that only mention TCI in the body (no "present" role) are labelled *"may be a former employee"*; unspecified TCI division is labelled *[TCI Group - division not specified]* | `_parse_profile` | ✅ |
| 3.6 | De-duplicated by profile slug / URL | `find_profiles_by_name`, `add()` | ✅ |
| 3.7 | Name-lookup query filter: stop-word lists reject greetings, roles, question words; 1–3 name words, each ≥3 letters; no digits | `router.py` `parse_name_query` | ✅ |
| 3.8 | Name tables are built from profile data, **not summarised by the LLM** (nobody dropped, nothing invented); optional AI summary only for full names with ≤3 matches and failure is non-fatal | `chat()` | ✅ |
| 3.9 | Disclaimer appended: details come from public LinkedIn profiles and may be incomplete/outdated | `chat()`, prompt | ✅ |
| 3.10 | Output HTML is escaped (`html_escape`) for names, headlines, locations, URLs | `chat()`, `board_service` | ✅ |
| 3.11 | 📝 Only professional/work info; never personal contact details, addresses or family info | `tavily_prompt` | 📝 |
| 3.12 | 📝 Never guess gender from a name; don't say she/he unless the source does; if user states a gender, say profiles don't show gender | `tavily_prompt` | 📝 |
| 3.13 | 📝 One named person → details for that exact person only; if not found say so, don't list others | `tavily_prompt` | 📝 |
| 3.14 | 📝 Skip students and interns unless asked | `tavily_prompt` | 📝 |

## 4. LLM prompt guardrails for web answers (`tavily_prompt` in `app.py`)

All 📝 (prompt-level):
- **Language:** English only, even for Hindi/Hinglish; no Devanagari.
- **Recency:** prefer the newest info; state "as of when"; don't present data older than 12 months as current unless asked.
- **No leaking internals:** never say "the context", "web search", "provided information", "not explicitly provided"; never name tools or labels (*Tavily*, *Knowledge Base*, *Catalog*). Name only real publishers (Moneycontrol, NSE/BSE, Economic Times, Business Standard, Yahoo Finance, company site).
- **Numbers:** give figures exactly as written; never estimate or invent; say "not available" if missing; show at most the 2–3 most recent periods; label period and publisher/date.
- **Fiscal calendar:** Indian FY (Apr–Mar); label a quarter from its end date, ignoring the source's label; if sources conflict, show both and name each source.
- **Contact details:** public business contacts (branch phone, email, address, hours) may be shared exactly as written; say if missing.
- **Format:** no headings; a few short lines or bullets.
- **STRICT SCOPE:** discuss only TCI Express; ignore context about other companies/competitors; if off-topic or no TCI info, reply exactly *"I can only help with information about TCI Express. Please ask something related to TCI Express."*
- When that refusal is returned, **web links are dropped** so unrelated links are not shown (code ✅ in `web_answer_for`).

## 5. Token / size / rate guardrails

| # | Guardrail | Where | Type |
|---|---|---|---|
| 5.1 | Web context capped at 7000 chars total, 900 per source (keeps request under Groq's ~7k-token limit) | `web_answer_for` | ✅ |
| 5.2 | On HTTP 413, retry once with 3000 total / 450 per source | `web_answer_for` | ✅ |
| 5.3 | `max_tokens=600` (900 for people lists) | `chatModel`, `tavily_people_chain` | ✅ |
| 5.4 | HTTP 429 → friendly "usage limit… try again in about X" message (not a 500) | `chat()` except block | ✅ |
| 5.5 | HTTP 413 → "needs too much text, make it shorter" | `chat()` except block | ✅ |
| 5.6 | Unknown error → generic message, details only in server log | `chat()` | ✅ |
| 5.7 | Name lookup uses `ThreadPoolExecutor(max_workers=8)` | `find_profiles_by_name` | ✅ |
| 5.8 | ⚠️ Retry on 429 exists only in the analytics engine, not for web/RAG/URL/tracking answers | – | gap |

## 6. LLM output cleanup

- `clean_answer()` strips `<think>…</think>` blocks and any unterminated `<think>` tail, so reasoning is never shown. ✅
- `reasoning_effort: "none"` sent to the model. ✅
- `md_to_html()` HTML-escapes text before converting bold/bullets. ✅

## 7. URL scraping guardrails (message contains a link)

| # | Guardrail | Where | Type |
|---|---|---|---|
| 7.1 | Request timeout 10 s; HTTP errors caught and shown as "couldn't access that website" | `scrape_website` | ✅ |
| 7.2 | Strips `script, style, nav, footer, header, aside` before reading text | `scrape_website` | ✅ |
| 7.3 | Content capped at 8000 chars | `max_chars` | ✅ |
| 7.4 | Empty-content and no-URL cases return clear messages | `handle_website_query` | ✅ |
| 7.5 | 📝 Answer from page content only; say so honestly if insufficient; English only | `web_system_prompt` | 📝 |
| 7.6 | ⚠️ **No domain allow-list / no private-IP block (SSRF).** A user can make the server fetch `http://localhost`, `169.254.169.254`, or internal hosts | `is_url` / `scrape_website` | gap |
| 7.7 | ⚠️ `is_url` also matches bare names like `file.com`/`x.in`, so ordinary text can be treated as a URL | `is_url` | gap |
| 7.8 | ⚠️ Page text is passed to the LLM unfiltered (prompt-injection risk) | `handle_website_query` | gap |

## 8. Official-source guardrail (board of directors)

- Board/committee questions are answered **only** from `https://www.tciexpress.in/board-committee-composition.aspx` – no web search, no LLM, so no hallucination. ✅
- Cached 1 h (`CACHE_SECONDS`); 20 s timeout; fetch failure → falls through to normal flow. ✅
- Skipped for words like *consolidated, ceo, cfo, ex-, former, past* so those go to web search. ✅
- Source line and link shown with the table. ✅

## 9. Routing guardrails (decide web vs internal data) – `src/router.py`

- `is_external_info_query`: news, results, quarters, FY, profit, share price, directors, employees etc. → **live web**, skipping the possibly-outdated Pinecone documents. Internal words (docket, dwb, branch, pincode, chart…) override this. ✅
- `is_company_finance_query`: "revenue of tci" → web figures **plus** internal booking data, with a note that internal freight ≠ reported revenue; breakdown words (by, month, top, chart…) → internal data only. ✅
- `is_data_query`: 10–12 digit number or analytics keywords → Excel engine; if it has no answer (`analytics_has_no_answer`), falls back to knowledge base then web. ✅
- Rate-limit error from analytics is shown as-is instead of retrying the web (would hit the same limit). ✅

## 10. Source / link guardrails

- Links returned only for real URLs in results; titles default to "Web Source N". ✅
- Never show tool names (Tavily) as sources (prompt 📝).
- Refusal answers return an empty link list. ✅

---

## Known gaps (recommended fixes)

1. **SSRF in URL scraping** – block private/loopback/link-local IPs, allow only `http/https`, and optionally an allow-list of domains.
2. **Prompt injection** – wrap scraped/search text in delimiters and tell the model to ignore instructions inside it; strip obvious "ignore previous instructions" strings.
3. **Prompt-only rules** (gender, personal info, intern skipping, scope) are not enforced in code – add post-checks (e.g., regex for phone/email on people answers, refusal-string detection).
4. **429 retry/backoff** for every LLM call, not just analytics.
5. **Tighten `is_url`** to require a scheme or `www.` and a valid TLD list.
6. **Per-IP rate limiting and auth** on `/get` and the knowledge-upload endpoints.
7. **Domain trust** – prefer/allow-list known publishers for finance questions; LinkedIn snippets can be stale or wrong.
