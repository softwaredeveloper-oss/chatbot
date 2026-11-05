import os
from datetime import datetime
import re
import uuid
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session

from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_openai import ChatOpenAI
from flask import Flask, render_template, request, jsonify
from html import escape as html_escape
from src.router import (
    is_company_finance_query,
    extract_person_name, parse_name_query, LIST_CUES,
)
from src.tracking_service import TCITrackingService
from src.board_service import is_board_query, board_answer

from src.helper import is_url, extract_url, scrape_website
from src.prompt import web_system_prompt
from web_search import WebSearchService, is_people_query

app = Flask(__name__)
# Behind nginx at a path prefix (e.g. /chatbot/): trust the X-Forwarded-* headers it sends
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_prefix=1)
# Signs the session cookie that remembers each user's last question (resets on restart)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.urandom(24)

tracking_service = TCITrackingService()
# ------------------- Load environment variables -------------------
load_dotenv()

required_keys = ["GROQ_API_KEY", "TAVILY_API_KEY"]
missing_keys = [key for key in required_keys if not os.getenv(key)]
if missing_keys:
    raise ValueError(f"Missing required env variables: {', '.join(missing_keys)}")

# ------------------- Initialize Web Search Service -------------------
web_search = WebSearchService()

# ------------------- Initialize LLM -------------------
chatModel = ChatOpenAI(
    model="qwen/qwen3.8-27b",
    temperature=0.3,
    max_tokens=600,
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
    model_kwargs={
        "reasoning_effort": "none"
    }
)

# ------------------- Web Analysis Prompt (URL-based) -------------------
web_prompt = PromptTemplate.from_template(web_system_prompt)

# ------------------- Tavily Fallback Prompt & Chain -------------------
tavily_prompt = ChatPromptTemplate.from_template(
    """You are a helpful and knowledgeable assistant for TCI Express.
Use the following live web search context to answer the user's question accurately and clearly.
Answer directly, as if you already know it. Never mention the "context", "web search", or "sources provided".
ALWAYS reply in English only, even if the user writes in Hindi or Hinglish. Never use Hindi words or Devanagari.

Today's date is {today}. Always prefer the MOST RECENT information. If the context has conflicting or dated
facts (board members, revenue, leadership), use the newest one and say as of when. Do not present information
older than 12 months as current unless the user explicitly asked about an earlier period.
When asked to compare "this year vs last year" (or any two periods), give a short side-by-side comparison:
the latest reported period vs the SAME period a year earlier with the % change, and the latest full fiscal year
vs the one before it, if the figures are available. State the numbers plainly (use crore or billion consistently).
Never say "the context", "the provided information", "not explicitly provided" or similar; if a figure is
missing, say simply "TCI Express has not published/I could not find X". Name only real publishers as sources
(e.g. Moneycontrol, NSE/BSE filing, Economic Times, Business Standard, Yahoo Finance, the company website);
never name search tools or aggregator labels such as "Tavily", "Knowledge Base" or "Catalog".
Do not use headings; keep the answer to a few short lines or bullets.
For financial results, revenue, profit, share price or news, give the figures exactly as written in the context,
and mention the period (e.g. Q2 FY26) and the publisher (e.g. Moneycontrol) and date when they are available.
Indian fiscal year runs April to March: Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar
(e.g. Q1 FY27 ended 30 June 2026). FY is named after the year it ENDS. Always label a quarter from its end date,
ignoring any label the sources use: ended Mar 2026 = Q4 FY26; Jun 2026 = Q1 FY27; Sep 2026 = Q2 FY27;
Dec 2026 = Q3 FY27; Mar 2027 = Q4 FY27. If sources give different figures for the same period, show both and name
the source of each. Show at most the 2-3 most recent periods, newest first.
Never estimate or invent numbers. If the context has no such figure, say it is not available.

BUSINESS CONTACT DETAILS: Public business contact details of TCI Express (branch/office/customer-care phone
numbers, email addresses, postal addresses, working hours) may be shared when they appear in the sources. Give the
exact number/address as written, name the branch or office it belongs to, and say if a detail is not listed.

PEOPLE: Questions about TCI Express employees, developers, the tech/IT team, managers, leaders or anyone who works
for or is associated with TCI Express are IN SCOPE. Answer them from the context (LinkedIn profiles, news, filings):
list EVERY distinct person named in the context (up to 15) as a bullet: **Name** - job title, department, location,
and any experience/tenure shown. Prefer people in the department the user asked about (e.g. IT/tech); if the context has
none from that department, still list the TCI Express employees you do have, and say which department they are in.
Never answer "no one found" when the context names TCI Express employees. Skip students and interns unless asked.
At the end add one short note that details come from public profiles (e.g. LinkedIn) and may be incomplete or outdated.
Only share professional/work information, never personal contact details, addresses or family information.
Do NOT refuse these questions.
If the question is about ONE named person (e.g. "Joyce Wilson"), give details only for that exact person if they appear
in the context as a TCI Express employee or associate: role, department, location, experience. If nobody with that
name appears in the context, say you could not find a person with that name at TCI Express - do not list other people.
NEVER guess a person's gender from their name. Do not say "women", "men", "she" or "he" unless the context itself says
so; use the person's name or "they". If the user states a gender, you may list the people but must say the profiles do
not show gender, so you cannot confirm it.

STRICT SCOPE: You only discuss TCI Express (Transport Corporation of India's express division), its services,
branches, shipments, policies, ESG, leadership, and the people who work for it. Use ONLY facts about TCI Express
from the context. Never give information about any other company, competitor, or organization, and ignore any
context that is about another company. If the question is not about TCI Express, or the context has nothing
about TCI Express on it, reply EXACTLY: "I can only help with information about TCI Express. Please ask something related to TCI Express."

Web Context:
{context}

Question: {question}

Answer:"""
)
tavily_chain = tavily_prompt | chatModel
# Lists of people need more room than the default 600-token limit
tavily_people_chain = tavily_prompt | chatModel.bind(max_tokens=900)

# ------------------- Config -------------------
MAX_MSG_LENGTH = 800

FALLBACK_MSG = (
    "Sorry, I couldn't find relevant information on the web. "
    "Could you please rephrase or ask something related to TCI Express? 🙂"
)
def clean_answer(text: str) -> str:
    if not text:
        return ""

    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    if "<think>" in text.lower():
        text = re.sub(
            r"<think>.*",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE
        )

    return text.strip()
def handle_website_query(msg: str) -> str:
    """Handles queries that contain a URL — scrapes and analyzes the site."""
    url = extract_url(msg)
    if not url:
        return "I couldn't detect a valid URL in your message. Please share a proper link."

    result = scrape_website(url)

    if not result["success"]:
        return f"Sorry, I couldn't access that website. Error: {result['error']}"

    if not result["content"]:
        return "The website loaded, but I couldn't find any readable text content on it."

    question_only = msg.replace(url, "").strip()
    if not question_only or len(question_only) < 5:
        question_only = "Summarize this website."

    formatted_prompt = web_prompt.format(
        title=result["title"],
        url=result["url"],
        content=result["content"]
    )

    full_input = f"{formatted_prompt}\n\nQuestion: {question_only}"

    response = chatModel.invoke(full_input)
    answer = clean_answer(response.content.strip())

    if not answer:
        answer = "I analyzed the website but couldn't generate a clear answer. Please try rephrasing your question."

    return answer
INTERNET_CUE = re.compile(r"\b(internet|online|google|web search|(from|on) the web|from web)\b", re.IGNORECASE)


def web_answer_for(msg: str):
    """Search the web for TCI Express info and answer from it. Returns (answer, links); ("", []) if nothing."""
    web_results = web_search.search_tciexpress(msg, max_results=3)
    if not web_results:
        return "", []

    web_links = [
        {"title": item.get("title") or f"Web Source {i}", "url": item.get("url", "")}
        for i, item in enumerate(web_results, 1) if item.get("url")
    ]

    def build_context(per_source: int, total: int) -> str:
        """Join the sources, cutting each one and the whole text so the request stays under Groq's size limit."""
        parts = []
        for idx, item in enumerate(web_results, 1):
            parts.append(
                f"SOURCE {idx}:\nTitle: {item.get('title', f'Web Source {idx}')}\n"
                f"URL: {item.get('url', '')}\nContent: {(item.get('content') or '')[:per_source]}"
            )
        return "\n\n".join(parts)[:total]

    chain = tavily_people_chain if is_people_query(msg) else tavily_chain
    today = datetime.now().strftime("%d %B %Y")

    # ~4 characters per token: 7000 chars of sources keeps the whole request well under the 7,000-token limit.
    # If Groq still says the request is too large (413), retry once with less text.
    try:
        res = chain.invoke({"context": build_context(900, 7000), "question": msg, "today": today})
    except Exception as e:
        if getattr(e, "status_code", None) != 413:
            raise
        print("[WARN] Request too large for the AI model; retrying with shorter sources")
        res = chain.invoke({"context": build_context(450, 3000), "question": msg, "today": today})
    answer = clean_answer(res.content.strip())

    if answer.startswith("I can only help with information about TCI Express"):
        web_links = []  # refusal: don't show unrelated links
    return answer, web_links


def md_to_html(text: str) -> str:
    """Tiny Markdown -> HTML (bold, bullets, paragraphs) for answers mixed with HTML tables."""
    out, in_list = [], False
    for line in html_escape(text).splitlines():
        s = line.strip()
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        if s.startswith(("* ", "- ")):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{s[2:]}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if s:
            out.append(f"<p style='margin:4px 0;'>{s}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


@app.route("/")
def index():
    return render_template("chat.html")
@app.route("/get", methods=["GET", "POST"])
def chat():
    msg = request.form.get("msg") or request.args.get("msg")
    if msg is None or not msg.strip():
        return jsonify({"error": "Please provide a valid question."}), 400

    msg = msg.strip()
    if len(msg) > MAX_MSG_LENGTH:
        return jsonify({
            "error": f"Your message is too long. Please limit it to {MAX_MSG_LENGTH} characters."
        }), 400

    # Short follow-ups like "search from linkedin" / "more" reuse the previous question
    last_q = session.get("last_q")
    is_followup = bool(
        last_q
        and not re.search(r"tci", msg, re.IGNORECASE)
        and (
            (len(msg.split()) <= 8
             and re.search(r"\b(linkedin|more|others?|again|continue|internet|online|google|web)\b", msg, re.IGNORECASE))
            # "i think she is in hr team" after asking about a person
            or (len(msg.split()) <= 12
                and extract_person_name(last_q)
                and re.search(r"\b(she|he|her|his|him|they|their)\b", msg, re.IGNORECASE))
        )
    )
    if is_followup:
        msg = f"{last_q} {msg}"
    else:
        session["last_q"] = msg

    # "find it from internet": skip our own data and search the internet directly
    force_web = bool(INTERNET_CUE.search(msg))
    if force_web:
        msg = re.sub(
            r"\b(please\s+)?(find|search|get|look|check)(\s+(it|this|that|for it))?(\s+up)?"
            r"(\s+(from|on|in|via|using))?(\s+the)?\s+(internet|web|online|google)\b",
            "", msg, flags=re.IGNORECASE,
        )
        msg = re.sub(r"\b(from|on|in|via)\s+(the\s+)?(internet|web|online|google)\b", "", msg, flags=re.IGNORECASE)
        msg = re.sub(r"\s+", " ", msg).strip()

    try:
        # 1. URL message direct handling
        if is_url(msg):
            answer = handle_website_query(msg)
            url_link = extract_url(msg)
            return jsonify({
                "answer": answer,
                "source": "Scraped URL",
                "links": [{"title": "Analyzed Website", "url": url_link}] if url_link else []
            }), 200
        # =========================================================================
        # 2. TCI EXPRESS LIVE SHIPMENT TRACKING
        # =========================================================================
        dwb_no = tracking_service.extract_dwb_no(msg)

        if dwb_no and tracking_service.is_tracking_question(msg):

            print(
                f"\n[INFO] >>> Routing to TCI Express Live Tracking: "
                f"DWB {dwb_no}"
            )

            tracking_result = tracking_service.track_dwb(dwb_no)

            if tracking_result.get("success"):

                return jsonify({
                    "type": "tracking",
                    "answer": tracking_result.get("answer", ""),
                    "source": "TCI Express Live Tracking",
                    "dwb_no": dwb_no,
                    "tracking": tracking_result.get("tracking", {}),
                    "booking_table": tracking_result.get(
                        "booking_table",
                        {}
                    ),
                    "movement_table": tracking_result.get(
                        "movement_table",
                        {}
                    ),
                    "links": [
                        {
                            "title": "TCI Express Tracking",
                            "url": tracking_result.get("url", "")
                        }
                    ]
                }), 200

            return jsonify({
                "type": "tracking_error",
                "answer": tracking_result.get(
                    "error",
                    f"Could not fetch tracking information for DWB {dwb_no}."
                ),
                "source": "TCI Express Live Tracking",
                "links": []
            }), 200    

        # Board of directors / committees: always read from the official TCI Express page
        if is_board_query(msg) and not re.search(r"\b(consolidated|ceo|cfo|ex-|former|past)\b", msg, re.IGNORECASE):
            board_html, board_links = board_answer(msg)
            if board_html:
                print(f"\n[INFO] >>> Board composition from official page: {msg}")
                return jsonify({
                    "answer": board_html,
                    "source": "TCI Express Official Website",
                    "links": board_links
                }), 200

        # "mohit" / "all mohit in tci": list EVERY matching public LinkedIn profile (built from the profile
        # data itself, not summarised by the LLM, so nobody is dropped and nothing is invented)
        name_q = parse_name_query(msg)
        if name_q:
            print(f"\n[INFO] >>> Name lookup (all matches): {name_q}")
            profiles = web_search.find_profiles_by_name(name_q)
            if profiles:
                shown = profiles[:60]
                rows = "".join(
                    "<tr><td>{i}</td><td><a href='{url}' target='_blank'>{name}</a></td>"
                    "<td>{headline}</td><td>{location}</td></tr>".format(
                        i=i, url=html_escape(p["url"], quote=True), name=html_escape(p["name"]),
                        headline=html_escape(p["headline"]), location=html_escape(p["location"]),
                    )
                    for i, p in enumerate(shown, 1)
                )
                more = f" Showing the first {len(shown)}." if len(profiles) > len(shown) else ""
                answer = (
                    f"<p style='margin:4px 0;'>Found <strong>{len(profiles)}</strong> public LinkedIn profile(s) "
                    f"named <strong>{html_escape(name_q)}</strong> at TCI Express.{more}</p>"
                    "<div class='chat-table-wrapper'><table class='dataframe table table-hover table-striped table-sm'>"
                    "<thead><tr><th>#</th><th>Name</th><th>Role</th><th>Location</th></tr></thead>"
                    f"<tbody>{rows}</tbody></table></div>"
                    "<p style='margin:4px 0;'><em>Details come from public LinkedIn profiles that the search finds. "
                    "The list may be incomplete or outdated.</em></p>"
                )
                # Full name (2+ words): add a short AI summary on top. It is optional - if the AI
                # service is rate-limited or fails, the table above is still returned.
                if len(name_q.split()) >= 2 and len(shown) <= 3:
                    try:
                        ctx = "\n\n".join(
                            f"SOURCE {i}:\nTitle: {p['name']} - {p['headline']}\nURL: {p['url']}\nContent: {p['snippet']}"
                            for i, p in enumerate(shown, 1)
                        )
                        res = tavily_chain.invoke({
                            "context": ctx, "question": msg,
                            "today": datetime.now().strftime("%d %B %Y"),
                        })
                        summary = clean_answer(res.content.strip())
                        if summary and not summary.startswith("I can only help"):
                            answer = md_to_html(summary) + "<hr>" + answer
                    except Exception as e:
                        print(f"[WARN] Name summary skipped: {e}")

                return jsonify({
                    "answer": answer,
                    "source": "Live Web Search",
                    "links": [{"title": p["name"], "url": p["url"]} for p in shown[:5]]
                }), 200

        # "revenue of tci": answer from the published figures on the internet
        if is_company_finance_query(msg):
            print(f"\n[INFO] >>> Company finance question, answering from web: {msg}")
            web_answer, web_links = web_answer_for(msg)
            if web_answer and not web_answer.startswith("I can only help with information about TCI Express"):
                return jsonify({
                    "answer": md_to_html(web_answer),
                    "source": "Live Web Search",
                    "links": web_links
                }), 200

        # =========================================================================
        # 5. Fallback: Live Web Search (Tavily)
        # =========================================================================
        print(f"[INFO] Triggering Web Search for: {msg}")

        # Whole-internet search, but only results that actually mention TCI Express are kept
        web_answer, web_links = web_answer_for(msg)

        if web_answer:
            return jsonify({
                "answer": web_answer,
                "source": "Live Web Search",
                "links": web_links
            }), 200

        # 6. Final Fallback
        return jsonify({
            "answer": FALLBACK_MSG,
            "source": "None",
            "links": []
        }), 200

    except Exception as e:
        print(f"[ERROR] Failed to process query '{msg}': {e}")

        # Groq limits (429 = too many tokens per minute/day, 413 = request too large): say so clearly
        status = getattr(e, "status_code", None)
        if status in (413, 429):
            if status == 429:
                wait = re.search(r"try again in ([0-9hms.]+)", str(e))
                when = f" Please try again in about {re.sub(r'\.\d+', '', wait.group(1)).rstrip('.')}." if wait else " Please try again in a few minutes."
                text = "The AI service has reached its usage limit right now." + when
            else:
                text = "That question needs too much text to process. Please make it shorter or more specific."
            return jsonify({"answer": text, "source": "None", "links": []}), 200

        return jsonify({
            "error": "Something went wrong while processing your request. Please try again."
        }), 500
    
if __name__ == "__main__":
    # 0.0.0.0 = listen on every network card, so the app is reachable at http://124.7.209.74:8000
    # Defaults suit running on this PC. On the Linux server (behind nginx) set APP_HOST=127.0.0.1 and APP_PORT=8002.
    app.run(host=os.getenv("APP_HOST", "0.0.0.0"), port=int(os.getenv("APP_PORT", "8000")), debug=False)