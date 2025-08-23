import re
import time
from typing import Optional

from fastapi import FastAPI, Body
from pydantic import BaseModel
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

app = FastAPI(title="Transcribe.mov helper")

class TranscribeReq(BaseModel):
    url: str                          # media URL to paste
    max_wait_sec: int = 600           # max time to wait for completion (default 10 min)

def _get_transcript_text(page) -> Optional[str]:
    """
    Try a few selectors to pull the final transcript block.
    Returns the text or None.
    """
    candidate_selectors = [
        # common “content” containers
        "div.prose",
        "div[class*='prose']",
        "div[class*='transcript']",
        "div[id*='transcript']",
        "article",
        # many sites place readable content under <main>
        "main",
        # fallback to container that holds lots of paragraphs
        "div.content, div.container, div.markdown"
    ]
    for sel in candidate_selectors:
        try:
            el = page.query_selector(sel)
            if not el:
                continue
            txt = el.inner_text().strip()
            # sanity-check: at least a few words
            if txt and len(txt.split()) > 8:
                return txt
        except Exception:
            continue

    # last fallback: collect visible paragraphs
    try:
        paras = page.query_selector_all("main p, article p, div p")
        txt = "\n\n".join([p.inner_text().strip() for p in paras if p.inner_text().strip()])
        return txt if len(txt.split()) > 8 else None
    except Exception:
        return None

@app.post("/transcribe")
def transcribe(req: TranscribeReq = Body(...)):
    """
    1) open app.transcribe.mov
    2) paste URL in the 'Download from anywhere' input
    3) click Submit
    4) wait for /transcript/... page and completion
    5) return transcript text
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        page = context.new_page()

        # STEP 1: Open the app
        page.goto("https://app.transcribe.mov/", wait_until="load")

        # STEP 2: Paste URL into the lower input (placeholder shows https://)
        # The input is the "Download from anywhere" box
        page.fill('input[placeholder^="https://"]', req.url)

        # STEP 3: Click the "Submit" button near that input
        # (target by button text)
        page.click("button:has-text('Submit')")

        # STEP 4: Wait to land on the result route /transcript/<id>
        try:
            page.wait_for_url(re.compile(r"/transcript/"), timeout=120_000)  # 120s
        except PWTimeout:
            browser.close()
            return {
                "status": "error",
                "message": "Did not navigate to /transcript/ page (maybe bad link or rate limit)."
            }

        # You’ll often see an interim page with “Started”
        # We don’t need to do anything except wait for completion or content.
        deadline = time.time() + req.max_wait_sec
        transcript_text: Optional[str] = None

        # Poll for completion text OR for transcript content to appear
        while time.time() < deadline and not transcript_text:
            try:
                # prefer explicit completion signal if present
                page.wait_for_selector("text=Transcription completed", timeout=5_000)
            except PWTimeout:
                pass  # it's okay; keep polling

            # Try to grab transcript text on every loop
            transcript_text = _get_transcript_text(page)
            if transcript_text:
                break

            # small idle wait between polls
            page.wait_for_timeout(1500)

        browser.close()

        if not transcript_text:
            return {
                "status": "error",
                "message": "Timed out waiting for transcript. Try increasing max_wait_sec or verify the link."
            }

        return {
            "status": "ok",
            "source_url": req.url,
            "transcript": transcript_text
        }
