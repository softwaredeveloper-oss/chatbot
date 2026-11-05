import re
import requests
from bs4 import BeautifulSoup


def is_url(text: str) -> bool:
    """Check if the message contains a URL."""
    url_pattern = re.compile(
        r'https?://[^\s]+|www\.[^\s]+|[a-zA-Z0-9-]+\.(com|in|org|net|io|co)[^\s]*'
    )
    return bool(url_pattern.search(text))


def extract_url(text: str) -> str:
    """Extract the first URL found in the text."""
    url_pattern = re.compile(
        r'https?://[^\s]+|www\.[^\s]+|[a-zA-Z0-9-]+\.(com|in|org|net|io|co)[^\s]*'
    )
    match = url_pattern.search(text)
    if not match:
        return None

    url = match.group(0)
    # Add https:// if missing
    if not url.startswith("http"):
        url = "https://" + url
    return url


def scrape_website(url: str, max_chars: int = 8000) -> dict:
    """
    Fetch a website and extract its readable text content.
    Returns dict with 'title', 'content', and 'success' status.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "lxml")

        # Remove script, style, nav, footer tags — not useful content
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title else "No title found"

        # Extract visible text
        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        content = "\n".join(lines)

        # Limit content length (avoid huge token usage)
        content = content[:max_chars]

        return {
            "success": True,
            "title": title,
            "content": content,
            "url": url
        }

    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "error": str(e),
            "url": url
        }
