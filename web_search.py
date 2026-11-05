import os
from typing import List, Dict, Any
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from dotenv import load_dotenv
from tavily import TavilyClient
from tavily.errors import ForbiddenError, UsageLimitExceededError

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


# Load environment variables from .env
load_dotenv()


class SearchLimitError(Exception):
    """The Tavily plan's search limit is used up (every search fails until it resets)."""

from src.router import bare_person_name, extract_person_name

TCI_PATTERN = re.compile(r"tci[\s\-]*express|tciexpress|tciexp\b", re.IGNORECASE)

OLDER_INFO_PATTERN = re.compile(
    r"\b(history|historical|past|previous(ly)?|earlier|former|old|older|founded|"
    r"since|over the years|years ago|all time|decade|archive)\b",
    re.IGNORECASE
)


# Stems (no trailing \b) so typos / variants like "devlopers", "employe", "engineeer" still match
PEOPLE_PATTERN = re.compile(
    r"\b(employ\w*|staff|worker\w*|people|personnel|team\w*|dev\w*per\w*|devs?\b|engin\w*|programm\w*|"
    r"managers?|heads?\b(?!\s*(office|quarter))|head of|hr\b|recruit\w*|executives?|leader\w*|work(s|ing|ed)?\b|"
    r"who is|who are|name of|names of|cto|cio|cfo|coo|ceo|vp\b|president|founder|"
    r"directors?|designers?|analysts?|tech\b|tech team|technical|technology team|it team|"
    r"linkedin|colleagues?|officers?)",
    re.IGNORECASE
)

# Extra searches that widen the pool of LinkedIn profiles for a given kind of person
ROLE_QUERIES = [
    (r"dev\w*per|devs?\b|engin|programm|tech|\bit\b|software|cto|cio|erp|digital",
     ["software developer", "IT manager", "application developer", "information technology", "ERP SAP developer"]),
    (r"sales|business|marketing|account",
     ["sales manager", "key account manager", "business development"]),
    (r"operation|logistic|transport|branch|supply",
     ["operations manager", "branch manager", "logistics"]),
    (r"hr|recruit|human",
     ["HR manager", "talent acquisition", "human resources"]),
    (r"financ|account|cfo|audit",
     ["finance manager", "accounts", "chief financial officer"]),
]


def is_people_query(query: str) -> bool:
    """True when the user asks about the people who work for / are linked to TCI Express."""
    return bool(PEOPLE_PATTERN.search(query) or bare_person_name(query))


def wants_older_info(query: str) -> bool:
    """True when the user explicitly asks about a period older than the last year."""
    if OLDER_INFO_PATTERN.search(query):
        return True
    current_year = datetime.now().year
    for y in re.findall(r"\b(?:19|20)\d{2}\b", query):
        if int(y) < current_year:
            return True
    for yy in re.findall(r"\bfy\s?-?(\d{2})\b", query, re.IGNORECASE):
        if 2000 + int(yy) < current_year:
            return True
    return False


# LinkedIn profiles write the company as "TCI", "TCI Express", "TCIExpress Ltd" ...
PROFILE_TCI_PATTERN = re.compile(
    r"\btci\b|tci[\s\-]*express|tciexpress|transport corporation of india", re.IGNORECASE
)

STATIC_INFO_PATTERN = re.compile(
    r"\b(phone|mobile|telephone|contact|number|email|e-mail|address|helpline|toll[\s-]*free|"
    r"customer care|branch|office|location|timings?|hours)\b",
    re.IGNORECASE,
)

EXPRESS_PATTERN = re.compile(r"tci[\s\-]*express|tciexpress|tci[\s\-]*xps|tcixps", re.IGNORECASE)
OTHER_TCI_PATTERN = re.compile(
    r"\btci\s+(supply\s*chain|freight|global|seaways|logistics|cold\s*chain|foundation|developers|infrastructure)",
    re.IGNORECASE,
)

NEWS_PATTERN = re.compile(
    r"news|latest|recent|update|announce|result|quarter|\bq[1-4]\b|fy\s?\d{2}|"
    r"revenue|profit|earnings|ebitda|dividend|share|stock|moneycontrol|"
    r"acquisition|expansion|launch|ipo|investor",
    re.IGNORECASE
)


def is_about_tci(result: dict) -> bool:
    """True if a search result mentions TCI Express in its title, URL or text."""
    text = f"{result.get('title', '')} {result.get('url', '')} {result.get('content', '')}"
    return bool(TCI_PATTERN.search(text))


class WebSearchService:
    """
    Service responsible for searching the web using Tavily.

    This class is kept separate from app.py so that web-search
    functionality can be reused later in RAG and Agentic AI.
    """

    # ---------------------------------------------------------
    # Initialize Tavily client
    # ---------------------------------------------------------
    def __init__(self):

        self.api_key = os.getenv("TAVILY_API_KEY")

        if not self.api_key:
            raise ValueError(
                "TAVILY_API_KEY is missing in the .env file."
            )

        self.client = TavilyClient(
            api_key=self.api_key
        )

    # ---------------------------------------------------------
    # General web search
    # ---------------------------------------------------------
    def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
        include_domains: List[str] = None,
        topic: str = "general",
        days: int = None,
        time_range: str = None
    ) -> List[Dict[str, Any]]:
        """
        Search the web using Tavily.

        Args:
            query: User's search question.
            max_results: Maximum number of results.
            search_depth: 'basic' or 'advanced'.
            include_domains: If given, Tavily only returns results from these domains.

        Returns:
            List of cleaned search results.
        """

        if not query or not query.strip():
            return []

        query = query.strip()

        try:

            params = dict(
                query=query,
                search_depth=search_depth,
                max_results=max_results,
                include_answer=False,
                include_raw_content=False,
                include_domains=include_domains or [],
                topic=topic
            )
            if topic == "news" and days:
                params["days"] = days
            if time_range:
                params["time_range"] = time_range

            response = self.client.search(**params)

            results = response.get("results", [])

            cleaned_results = []

            for result in results:

                cleaned_results.append({
                    "title": result.get("title", ""),
                    "url": result.get("url", ""),
                    "content": result.get("content", ""),
                    "score": result.get("score", 0)
                })

            return cleaned_results

        except Exception as e:
            # Plan limit used up (Tavily sends 429 / 432 / or 403 "exceeds your plan's set usage limit").
            # That is not "no results": tell the caller, so the user is not told the info does not exist
            if isinstance(e, UsageLimitExceededError) or (
                isinstance(e, ForbiddenError) and "usage limit" in str(e).lower()
            ):
                print(f"[WEB SEARCH ERROR] Tavily usage limit reached: {e}")
                raise SearchLimitError(str(e)) from e

            print(f"[WEB SEARCH ERROR] {e}")

            return []

    # ---------------------------------------------------------
    # Search only TCIEXPRESS website
    # ---------------------------------------------------------
    def search_tciexpress(
        self,
        query: str,
        max_results: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search specifically for TCIEXPRESS-related information.

        The search query is restricted to tciexpress.in.
        """

        if not query or not query.strip():
            return []

        tci_query = f"TCI Express {query.strip()}"

        # People / employee questions: LinkedIn profiles first, no date limit
        # (profiles have no publish date, so a date filter would drop them all)
        if is_people_query(query):
            max_results = max(max_results, 12)
            q = query.strip()

            # Several differently-worded LinkedIn searches => many more distinct profiles
            person = extract_person_name(q)
            if person:
                # Looking for one specific person: search for that exact name first
                linkedin_queries = [f"{person} at TCI Express LinkedIn", f"{person} TCI Express"]
            else:
                linkedin_queries = [f"{q} at TCI Express LinkedIn"]

            # A bare name needs no extra role searches; otherwise widen with role-based ones
            if not bare_person_name(q):
                for role_regex, roles in ROLE_QUERIES:
                    if re.search(role_regex, q, re.IGNORECASE):
                        linkedin_queries += [f"{role} at TCI Express LinkedIn" for role in roles]
                        break
                else:
                    if not person:
                        linkedin_queries += ["employee at TCI Express LinkedIn", "manager at TCI Express LinkedIn"]

            merged, seen = [], set()

            def add(batch):
                for r in batch:
                    if r["url"] not in seen:
                        seen.add(r["url"])
                        merged.append(r)

            for lq in linkedin_queries:
                add(self.search(
                    query=lq,
                    max_results=10,
                    search_depth="advanced",
                    include_domains=["linkedin.com"]
                ))

            # Plus the general web (company site, news, filings)
            add(self.search(
                query=f"{q} TCI Express employees team",
                max_results=10,
                search_depth="basic"
            ))

            # Other TCI Group companies (Freight, Supply Chain, ...) are not TCI Express: drop them
            # unless the result also clearly names TCI Express / TCI XPS
            def is_other_tci_group(r):
                text = f"{r.get('title', '')} {r.get('content', '')}"
                return bool(OTHER_TCI_PATTERN.search(text)) and not EXPRESS_PATTERN.search(text)

            return [r for r in merged if is_about_tci(r) and not is_other_tci_group(r)][:max_results]

        # Default: only the last 12 months. Older data only if the user asks for it.
        # Contact / address pages (branches, helplines) carry no publish date, so don't date-filter them
        old_requested = wants_older_info(query) or bool(STATIC_INFO_PATTERN.search(query))
        time_range = None if old_requested else "year"

        # Official website first: for ordinary questions search only tciexpress.in (exact, no third-party
        # pages). News / financial / dated questions need outside publishers, so they skip this step.
        # If the official site has nothing, fall through to the whole-web search below.
        if not wants_older_info(query) and not NEWS_PATTERN.search(query):
            official = self.search(
                query=tci_query,
                max_results=max_results,
                search_depth="basic",
                include_domains=["tciexpress.in"],
            )
            if official:
                return official[:max_results]

        # Fetch extra results, then keep only the ones actually about TCI Express
        results = self.search(
            query=tci_query,
            max_results=max_results * 3,
            search_depth="basic",
            time_range=time_range
        )

        # News / financial questions also get a dedicated news search,
        # so Moneycontrol, Economic Times, etc. articles on TCI Express show up
        if NEWS_PATTERN.search(query):
            news = self.search(
                query=tci_query,
                max_results=max_results * 3,
                search_depth="basic",
                topic="news",
                days=None if old_requested else 365
            )
            seen = {r["url"] for r in news}
            results = news + [r for r in results if r["url"] not in seen]

        return [r for r in results if is_about_tci(r)][:max_results]

    # ---------------------------------------------------------
    # List every public LinkedIn profile matching a (first) name
    # ---------------------------------------------------------
    DEPARTMENT_HINTS = [
        "sales", "marketing", "operations", "manager", "executive", "officer", "transport",
        "accounts", "software IT", "HR", "branch", "customer service", "logistics", "supervisor",
    ]

    @staticmethod
    def _parse_profile(result: Dict[str, Any], name_tokens: List[str]):
        """Turn one LinkedIn search result into {name, headline, location, url}; None if not a match."""
        url = result.get("url", "")
        m = re.search(r"linkedin\.com/in/([^/?#]+)", url)
        if not m:
            return None

        raw_title = result.get("title", "")
        title = re.sub(r"\s*[|\-–—]\s*LinkedIn.*$", "", raw_title, flags=re.IGNORECASE).strip()
        parts = [p.strip() for p in re.split(r"\s+-\s+", title) if p.strip()]
        if not parts:
            return None
        full_name = parts[0]
        # every searched name token must be a whole word in the NAME part of the title
        if not all(re.search(rf"\b{re.escape(t)}\b", full_name, re.IGNORECASE) for t in name_tokens):
            return None
        content = result.get("content", "")
        # Profiles often just say "TCI" (e.g. "Asst. Manager at TCI"), so accept that too
        if not PROFILE_TCI_PATTERN.search(f"{raw_title} {content}"):
            return None

        # Other TCI Group companies (Supply Chain, Freight, ...) are not TCI Express: skip them,
        # unless the profile also clearly names TCI Express / TCI XPS
        text = f"{raw_title} {content}"
        is_express = bool(EXPRESS_PATTERN.search(text))
        if OTHER_TCI_PATTERN.search(text) and not is_express:
            return None

        headline = " - ".join(parts[1:])
        if not headline:
            h = re.search(r"Headline:\s*([^\n]+)", content) or re.search(r"Current role:\s*([^\n]+)", content)
            headline = h.group(1).strip() if h else ""

        # "... at TCI (May 2006 to present)" => currently employed there
        current = re.search(r"\bat\s+(TCI[A-Za-z0-9 .&-]*?)\s*\(([^)]*?)\s+(?:to|-)\s+present\)", content, re.IGNORECASE)
        if current:
            since = current.group(2).strip()
            if not PROFILE_TCI_PATTERN.search(headline):
                headline = (headline + " " if headline else "") + f"(at {current.group(1).strip()} since {since})"
        elif not PROFILE_TCI_PATTERN.search(f"{full_name} {headline}"):
            # TCI only in the profile body, with no "present" role => may be a former employee
            headline = (headline + " " if headline else "") + "(mentions TCI in profile; may be a former employee)"

        if not is_express:
            headline += " [TCI Group - division not specified]"
        loc = re.search(r"Location:\s*([^·|\n]+?)(?:\s*[·|\n]|$)", content)
        return {
            "slug": m.group(1).lower(),
            "name": full_name,
            "headline": headline,
            "location": loc.group(1).strip() if loc else "",
            "url": f"https://www.linkedin.com/in/{m.group(1)}",
            "snippet": content[:1000],
        }

    def find_profiles_by_name(self, name: str) -> List[Dict[str, Any]]:
        """
        Find (as many as possible) public LinkedIn profiles of TCI Express people with this name.
        One quick check first; only if it finds someone do we run the wider set of searches.
        """
        name = (name or "").strip()
        if not name:
            return []
        tokens = name.lower().split()

        def run(q: str):
            return self.search(
                query=q, max_results=10, search_depth="basic", include_domains=["linkedin.com"]
            )

        found: Dict[str, Dict[str, Any]] = {}

        def collect(results):
            for r in results:
                p = self._parse_profile(r, tokens)
                if p and p["slug"] not in found:
                    found[p["slug"]] = p

        collect(run(f"{name} TCI Express LinkedIn"))
        if not found:
            return []  # not a person search after all (saves the extra searches)

        queries = [f"{name} at TCI Express", f"{name} TCIExpress Ltd"]
        queries += [f"{name} {d} TCI Express LinkedIn" for d in self.DEPARTMENT_HINTS]
        with ThreadPoolExecutor(max_workers=8) as pool:
            for results in pool.map(run, queries):
                collect(results)

        return sorted(found.values(), key=lambda p: p["name"].lower())

    # ---------------------------------------------------------
    # Convert search results into LLM-friendly context
    # ---------------------------------------------------------
    def get_web_context(
        self,
        query: str,
        max_results: int = 5,
        tciexpress_only: bool = False
    ) -> str:
        """
        Search the web and convert results into a single text
        context that can later be provided to the LLM.

        Args:
            query: Search query.
            max_results: Maximum search results.
            tciexpress_only: If True, search only tciexpress.in.

        Returns:
            Formatted web context string.
        """

        if tciexpress_only:

            results = self.search_tciexpress(
                query=query,
                max_results=max_results
            )

        else:

            results = self.search(
                query=query,
                max_results=max_results,
                search_depth="basic"
            )

        if not results:
            return ""

        context_parts = []

        for index, result in enumerate(results, start=1):

            title = result.get("title", "")
            url = result.get("url", "")
            content = result.get("content", "")
            score = result.get("score", 0)

            context_parts.append(
                f"""
WEB SOURCE {index}

Title:
{title}

URL:
{url}

Relevance Score:
{score}

Content:
{content}
""".strip()
            )

        return "\n\n".join(context_parts)

    # ---------------------------------------------------------
    # Convert Tavily search results into LangChain Documents
    # ---------------------------------------------------------
    def search_as_documents(
        self,
        query: str,
        max_results: int = 5,
        tciexpress_only: bool = True
    ) -> List[Document]:
        """
        Search the web and convert each result into a
        LangChain Document.

        This will be useful when we want to send web data
        into the same RAG/Pinecone pipeline.
        """

        if tciexpress_only:

            results = self.search_tciexpress(
                query=query,
                max_results=max_results
            )

        else:

            results = self.search(
                query=query,
                max_results=max_results
            )

        documents = []

        for result in results:

            content = result.get("content", "").strip()

            if not content:
                continue

            document = Document(
                page_content=content,
                metadata={
                    "source": result.get("url", ""),
                    "title": result.get("title", ""),
                    "score": result.get("score", 0),
                    "type": "web"
                }
            )

            documents.append(document)

        return documents

    # ---------------------------------------------------------
    # Split web documents into smaller chunks
    # ---------------------------------------------------------
    def split_documents(
        self,
        documents: List[Document],
        chunk_size: int = 500,
        chunk_overlap: int = 50
    ) -> List[Document]:
        """
        Split web documents into smaller chunks.

        These chunks can later be converted into embeddings
        and stored in Pinecone.
        """

        if not documents:
            return []

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )

        return text_splitter.split_documents(documents)
