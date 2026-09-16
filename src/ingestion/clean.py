"""
Clean raw SEC EDGAR inline-XBRL HTML filings into plain readable text.

Strips XBRL tags, scripts/styles, and repeated legal boilerplate, while
preserving line breaks around block-level elements so that SEC "Item N"
section headers remain detectable as distinct lines for the chunker.
"""
import re
import warnings
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

BLOCK_TAGS = ["p", "div", "tr", "br", "h1", "h2", "h3", "h4", "li", "table"]


def html_to_text(raw_html: str) -> str:
    """Convert one raw filing .htm file's contents into cleaned plain text."""
    soup = BeautifulSoup(raw_html, "lxml")

    # Drop non-visible / non-content elements entirely.
    for tag in soup(["script", "style", "head", "ix:header", "ix:hidden"]):
        tag.decompose()

    # Insert newlines at block boundaries so paragraph/row structure survives
    # the text extraction (otherwise BeautifulSoup collapses everything).
    for tag in soup.find_all(BLOCK_TAGS):
        tag.append("\n")

    text = soup.get_text()

    # Normalize whitespace: collapse runs of spaces/tabs, cap blank lines.
    text = text.replace("\xa0", " ").replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)
    text = re.sub(r" *\n *", "\n", text)

    lines = [ln.strip() for ln in text.split("\n")]
    # Drop empty lines and lines that are pure noise (single stray characters,
    # page-number-only lines, table-of-contents dot leaders).
    cleaned_lines = []
    for ln in lines:
        if not ln:
            continue
        if re.fullmatch(r"[\.\-–—_ ]{3,}", ln):
            continue
        if re.fullmatch(r"\d{1,4}", ln):
            # bare page number
            continue
        cleaned_lines.append(ln)

    text = "\n".join(cleaned_lines)
    # Collapse duplicate consecutive lines (repeated legal disclaimers/table headers).
    dedup_lines = []
    prev = None
    for ln in text.split("\n"):
        if ln == prev:
            continue
        dedup_lines.append(ln)
        prev = ln
    return "\n".join(dedup_lines)


def clean_filing_file(path: str) -> str:
    with open(path, "r", errors="ignore") as f:
        raw = f.read()
    return html_to_text(raw)


if __name__ == "__main__":
    import sys
    out = clean_filing_file(sys.argv[1])
    print(out[:3000])
    print("\n\n...TOTAL LEN:", len(out))
