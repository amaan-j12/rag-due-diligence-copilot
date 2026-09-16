"""
Downloads 10-K / 10-Q filings from SEC EDGAR for a fixed list of 30 companies
across tech, pharma, and financial services. Free, public, no API key.

Rate limit: SEC allows 10 req/sec; this script sleeps 0.15s between requests
to stay safely under that.
"""
import json
import os
import time
import urllib.request

HEADERS = {"User-Agent": "Amaan Portfolio Project research@example.com"}
BASE_DIR = "/Users/mohammadamaan/Claude Projects/Content OS/rag-due-diligence-copilot/data/raw"
TICKERS = {
    # tech
    "AAPL": "tech", "MSFT": "tech", "NVDA": "tech", "CRM": "tech", "ADBE": "tech",
    "ORCL": "tech", "IBM": "tech", "INTC": "tech", "CSCO": "tech", "AMD": "tech",
    # pharma
    "PFE": "pharma", "JNJ": "pharma", "MRK": "pharma", "LLY": "pharma", "ABBV": "pharma",
    "BMY": "pharma", "GILD": "pharma", "AMGN": "pharma", "MRNA": "pharma", "REGN": "pharma",
    # financial services
    "JPM": "financial", "GS": "financial", "BAC": "financial", "MS": "financial", "WFC": "financial",
    "C": "financial", "AXP": "financial", "BLK": "financial", "SCHW": "financial", "USB": "financial",
}


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def get_cik_map():
    data = json.loads(fetch("https://www.sec.gov/files/company_tickers.json"))
    return {v["ticker"]: str(v["cik_str"]).zfill(10) for v in data.values()}


def main():
    cik_map = get_cik_map()
    manifest = []

    for ticker, sector in TICKERS.items():
        cik = cik_map.get(ticker)
        if not cik:
            print(f"SKIP {ticker}: no CIK found")
            continue

        time.sleep(0.15)
        try:
            submissions = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{cik}.json"))
        except Exception as e:
            print(f"SKIP {ticker}: submissions fetch failed ({e})")
            continue

        recent = submissions["filings"]["recent"]
        company_name = submissions.get("name", ticker)
        out_dir = os.path.join(BASE_DIR, ticker)
        os.makedirs(out_dir, exist_ok=True)

        picked_10k = 0
        picked_10q = 0
        for i, form in enumerate(recent["form"]):
            if form == "10-K" and picked_10k < 1:
                target, picked_10k = "10-K", picked_10k + 1
            elif form == "10-Q" and picked_10q < 2:
                target, picked_10q = "10-Q", picked_10q + 1
            else:
                continue

            accession = recent["accessionNumber"][i].replace("-", "")
            doc = recent["primaryDocument"][i]
            filing_date = recent["filingDate"][i]
            doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{doc}"

            time.sleep(0.15)
            try:
                content = fetch(doc_url)
            except Exception as e:
                print(f"  FAILED {ticker} {form} {filing_date}: {e}")
                continue

            filename = f"{ticker}_{form}_{filing_date}.htm"
            filepath = os.path.join(out_dir, filename)
            with open(filepath, "wb") as f:
                f.write(content)

            manifest.append({
                "ticker": ticker, "sector": sector, "company": company_name,
                "form": form, "filing_date": filing_date,
                "source_url": doc_url, "local_path": filepath,
                "size_bytes": len(content),
            })
            print(f"  OK {ticker} {form} {filing_date} ({len(content)} bytes)")

            if picked_10k >= 1 and picked_10q >= 2:
                break

    manifest_path = os.path.join(BASE_DIR, "..", "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nDone. {len(manifest)} filings downloaded across {len(TICKERS)} companies.")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
