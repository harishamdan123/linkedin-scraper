import re
import time
from typing import Optional

from fastapi import FastAPI, Body
from pydantic import BaseModel
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

app = FastAPI(title="Transcribe.mov API")


# ---------- MODELS ----------
class TranscribeReq(BaseModel):
    url: str                          # media URL to paste
    max_wait_sec: int = 600           # max time to wait for completion (default 10 min)


# ---------- HELPERS ----------
def _get_transcript_text(page) -> Optional[str]:
    """
    Try several selectors to pull the final transcript block.
    Returns the text or None if not found.
    """
    candidate_selectors = [
        "div.prose",
        "div[class*='prose']",
        "div[class*='transcript']",
        "div[id*='transcript']",
        "article",
        "main",
        "div.content, div.container, div.markdown"
    ]
    for sel in candidate_selectors:
        try:
            el = page.query_selector(sel)
            if not el:
                continue
            txt = el.inner_text().strip()
            if txt and len(txt.split()) > 8:  # must have some words
                return txt
        except Exception:
            continue

    # fallback: collect visible paragraphs
    try:
        paras = page.query_selector_all("main p, article p, div p")
        txt = "\n\n".join([p.inner_text().strip() for p in paras if p.inner_text().strip()])
        return txt if len(txt.split()) > 8 else None
    except Exception:
        return None


# ---------- ROUTES ----------
@app.get("/")
def root():
    return {"status": "ok", "message": "Use POST /transcribe or open /docs"}


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/transcribe")
def transcribe(req: TranscribeReq = Body(...)):
    """
    Automates app.transcribe.mov:
    1) open site
    2) paste URL in 'Download from anywhere'
    3) click Submit
    4) wait for /transcript/ page
    5) wait for transcript to complete
    6) return transcript text
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        page = context.new_page()

        # Step 1: Open the app
        page.goto("https://app.transcribe.mov/", wait_until="load")

        # Step 2: Fill the media URL
        page.fill('input[placeholder^="https://"]', req.url)

        # Step 3: Click the Submit button
        page.click("button:has-text('Submit')")

        # Step 4: Wait for result page
        try:
            page.wait_for_url(re.compile(r"/transcript/"), timeout=120_000)
        except PWTimeout:
            browser.close()
            return {
                "status": "error",
                "message": "Did not navigate to /transcript/ page. Maybe bad link or rate limit."
            }

        # Step 5: Poll until transcript is ready or timeout
        deadline = time.time() + req.max_wait_sec
        transcript_text: Optional[str] = None

        while time.time() < deadline and not transcript_text:
            try:
                page.wait_for_selector("text=Transcription completed", timeout=5_000)
            except PWTimeout:
                pass

            transcript_text = _get_transcript_text(page)
            if transcript_text:
                break

            page.wait_for_timeout(1500)  # short wait

        browser.close()

        if not transcript_text:
            return {
                "status": "error",
                "message": "Timed out waiting for transcript. Try longer max_wait_sec or verify link."
            }

        return {
            "status": "ok",
            "source_url": req.url,
            "transcript": transcript_text
        }
