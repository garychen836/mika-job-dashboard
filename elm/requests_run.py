"""Search the most-asked pending requests (what students looked for and did not find), within the same caps as the page's search.

    python elm/requests_run.py --max 5
"""
import argparse, asyncio, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import search

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=5)
    a = ap.parse_args()
    for line in asyncio.run(search.run_requests(a.max)):
        print(line)
