import re

# Company-level / market information that lives on the internet, not in our own documents
EXTERNAL_INFO_PATTERN = re.compile(
    r"\b(news|latest|recent|update|updates|announce\w*|press release|moneycontrol|"
    r"results?|quarter\w*|q[1-4]|fy\s?\d{2,4}|annual report|profit|pat|ebitda|"
    r"earnings?|dividend|share price|stock|shares?|market cap|nse|bse|tciexp|"
    r"investor|valuation|acquisition|ceo|md|director|management|ipo|"
    r"financials?|revenue report|growth|guidance|outlook)\b"
    r"|\btci(\s+express)?\s+(revenue|turnover|sales|business)\b"
)

# Words that only make sense for our internal docket / pincode wording
INTERNAL_DATA_PATTERN = re.compile(
    r"\b(docket|dwb|branch|booking|consignor|consignee|lane|pincode|pin code|"
    r"div|division|chart|graph|plot|top \d+|oda|actwt|crgwt|pkgs?)\b"
)


# Words that rule out "this is a person's name" (greetings, roles, common question words)
NAME_STOPWORDS = {
    "hi", "hello", "hey", "thanks", "thank", "you", "ok", "okay", "bye", "good", "morning", "afternoon",
    "evening", "night", "please", "help", "what", "how", "why", "when", "where", "who", "which", "is", "are",
    "was", "the", "a", "an", "of", "for", "to", "in", "on", "at", "and", "or", "tci", "express", "service",
    "services", "track", "tracking", "status", "docket", "shipment", "news", "latest", "revenue", "profit",
    "share", "price", "chief", "information", "officer", "manager", "director", "human", "resources",
    "regional", "assistant", "key", "account", "national", "software", "engineer", "developer", "head",
    "team", "employee", "employees", "people", "company", "about", "tell", "me", "show", "give", "details",
    "detail", "contact", "address", "phone", "email", "website", "hr", "it", "linkedin", "search", "find",
    "more", "i", "my", "your", "we", "can", "do", "does", "yes", "no", "sorry", "welcome", "name", "names",
    "list", "she", "he", "her", "his", "they", "think", "this", "that", "there", "here", "with", "from",
}

_BARE_NAME = re.compile(
    r"(?:(?:who is|who's|profile of|find|search for)\s+)?([A-Za-z][A-Za-z.'-]+(?:\s+[A-Za-z][A-Za-z.'-]+){1,2})",
    re.IGNORECASE,
)


def bare_person_name(text: str):
    """'Joyce Wilson' / 'who is Joyce Wilson' -> 'Joyce Wilson'; anything else -> None."""
    m = _BARE_NAME.fullmatch((text or "").strip().rstrip("?.! "))
    if not m:
        return None
    words = m.group(1).split()
    if any(w.lower().strip(".") in NAME_STOPWORDS for w in words):
        return None
    return " ".join(w.capitalize() for w in words)


LIST_CUES = re.compile(
    r"\b(all|every|everyone|everybody|list|named|called|whose name|how many|searching|search|looking|look|"
    r"find|show|tell|who|info|information|details|profile)\b",
    re.IGNORECASE,
)
EXTRA_FILLERS = {
    "all", "every", "everyone", "everybody", "list", "named", "called", "whose", "any", "anyone", "ltd",
    "limited", "tciexpress", "working", "persons", "person", "display", "get", "many", "there", "tci",
    "express", "in", "at", "of", "with", "name", "names", "employees", "employee", "people", "staff",
    "work", "works", "worked", "working",
    # "i'm searching ashish nigam in tech": phrasing and department words are not part of the name
    "i'm", "im", "am", "searching", "looking", "look", "for", "on", "regarding", "tech", "technology",
    "department", "dept", "team", "division", "sales", "marketing", "hr", "operations", "it", "his", "her",
}


def parse_name_query(text: str):
    """
    Pull a (partial) person name out of messages like 'mohit', 'all mohit in tci',
    'list all people named Mohit' -> 'Mohit'. Returns None if it doesn't look like a name lookup.
    """
    if not text or re.search(r"\d", text):
        return None
    words = re.findall(r"[A-Za-z][A-Za-z.'-]*", text)
    if not words or len(words) > 10:
        return None
    kept = [w for w in words if w.lower().strip(".") not in NAME_STOPWORDS and w.lower() not in EXTRA_FILLERS]
    if not 1 <= len(kept) <= 3 or any(len(w) < 3 for w in kept):
        return None
    # Short message ("mohit") or an explicit listing cue ("all mohit in tci")
    if len(words) > 3 and not LIST_CUES.search(text):
        return None
    return " ".join(w.capitalize() for w in kept)


def extract_person_name(text: str):
    """Bare name, or a Capitalized Name embedded in a longer sentence ('tell me about Joyce Wilson')."""
    name = bare_person_name(text)
    if name:
        return name
    for m in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b", text or ""):
        if not any(w.lower() in NAME_STOPWORDS for w in m.group(1).split()):
            return m.group(1)
    return None


# Company-level money questions ("revenue of tci") answered from published (web) figures.
# Breakdown words mean a detailed report, which the chatbot does not provide.
COMPANY_FINANCE_PATTERN = re.compile(r"\b(revenue|turnover|sales|income|business)\b", re.IGNORECASE)
BREAKDOWN_PATTERN = re.compile(
    r"\b(by|per|each|wise|month|monthly|week|weekly|day|daily|customer|customers|state|city|region|zone|"
    r"area|top|highest|lowest|average|avg|compare|trend|july|jul|june|aug|chart|graph|plot|table)\b",
    re.IGNORECASE,
)


def is_company_finance_query(query: str) -> bool:
    q = query or ""
    return bool(
        COMPANY_FINANCE_PATTERN.search(q)
        and not INTERNAL_DATA_PATTERN.search(q.lower())
        and not BREAKDOWN_PATTERN.search(q)
    )


def is_external_info_query(query: str) -> bool:
    """News / financials / leadership questions: answer from the live web, not the old knowledge base."""
    q = query.lower()
    if INTERNAL_DATA_PATTERN.search(q):
        return False
    if bare_person_name(query):
        return True
    return bool(EXTERNAL_INFO_PATTERN.search(q) or re.search(
        r"\b(board|members?|leadership|promoters?|employ\w*|staff|dev\w*per\w*|devs?|engin\w*|team|people|"
        r"managers?|heads?|hr|cto|cio|cfo|coo|linkedin|works?|working|personnel)\b", q))


# ---------------------------------------------------------------------------
# Questions about the booking / docket / branch / customer / pincode data (answered from Oracle)
# ---------------------------------------------------------------------------
DB_STRONG = re.compile(
    r"\b(dockets?|dwb|bookings?|booked|consignors?|consignees?|freight|tfrt|chargeable|pincodes?|pin\s+codes?|oda)\b",
    re.IGNORECASE,
)
DB_WEAK = re.compile(
    r"\b(revenue|weights?|packages?|pkgs?|rows?|records?|entries|customers?|clients?|parties|orders?|ordere?d|odered|shipped|shippers?|"
    r"senders?|branch(?:es)?|states?|cit(?:y|ies)|region|zone|division)\b",
    re.IGNORECASE,
)
DB_AGG = re.compile(
    r"\b(how many|count|total|sum|average|avg|top|highest|lowest|maximum|minimum|number of|list|show|which|"
    r"who|most|least|best|biggest|largest|per|by|wise|each|compare|trend|monthly|yearly|month|year)\b",
    re.IGNORECASE,
)
# "how do I book a shipment", "booking process" are website questions, not data questions
DB_HOWTO = re.compile(
    r"\b(how (to|do|can|should) (i|we|you)|process|procedure|steps? to|apply|book a|insurance policy)\b",
    re.IGNORECASE,
)
DWB_NUMBER = re.compile(r"\b\d{8,15}\b")


def is_db_query(query: str) -> bool:
    """True when the question is about our own booking data (dockets, freight, branches, customers, pincodes)."""
    q = query or ""
    if DB_HOWTO.search(q) or is_company_finance_query(q):
        return False  # website question / "revenue of tci" (published figures come from the web)
    if DB_STRONG.search(q) or DWB_NUMBER.search(q):
        return True
    return bool(DB_WEAK.search(q) and DB_AGG.search(q))
