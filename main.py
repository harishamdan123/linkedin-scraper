import re
import time
from typing import Optional, List

from fastapi import FastAPI, Body, Form, Query
from pydantic import BaseModel
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

app = FastAPI(title="Transcribe.mov API")


# ---------- MODELS ----------
class TranscribeReq(BaseModel):
    url: str                          # media URL to paste
    max_wait_sec: int = 600           # max time to wait (default 10 min)


# ---------- HELPERS ----------
def _extract_big_text(page) -> Optional[str]:
    """
    Try several selectors to pull the final transcript block.
    Returns the text or None if not found / too small.
    """
    candidate_selectors = [
        "div.prose",
        "div[class*='prose']",
        "div[class*='transcript']",
        "div[id*='transcript']",
        "article",
        "main",
        "div.content, div.container, div.markdown",
    ]
    for sel in candidate_selectors:
        try:
            el = page.query_selector(sel)
            if not el:
                continue
            txt = el.inner_text().strip()
            # require enough words to avoid returning the 'Started...' message
            if txt and len(txt.split()) > 100:
                return txt
        except Exception:
            continue

    # fallback: collect visible paragraphs
    try:
        paras = page.query_selector_all("main p, article p, div p")
        txt = "\n\n".join([p.inner_text().strip() for p in paras if p.inner_text().strip()])
        return txt if len(txt.split()) > 100 else None
    except Exception:
        return None


def _run_transcription(url: str, max_wait_sec: int, phase_log: List[str]):
    with sync_playwright() as p:
        # --no-sandbox improves compatibility on many hosts
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        page = context.new_page()

        # Step 1: Open the app
        phase_log.append("open_app")
        page.goto("https://app.transcribe.mov/", wait_until="load")

        # Step 2: Fill the media URL (the 'Download from anywhere' input)
        phase_log.append("fill_url")
        filled = False
        selectors = [
            'input[placeholder^="https://"]',
            "input[type=url]",
            "input[placeholder*='https']",
        ]
        for sel in selectors:
            el = page.query_selector(sel)
            if el:
                el.fill(url)
                filled = True
                break
        if not filled:
            # fallback: locate by section heading
            try:
                section = page.locator("text=Download from anywhere").first
                inp = section.locator("xpath=..").locator("input").first
                inp.fill(url)
                filled = True
            except Exception:
                pass

        if not filled:
            browser.close()
            return {
                "status": "error",
                "message": "Could not find the URL input on the page.",
                "phase_log": phase_log,
            }

        # Step 3: Click the Submit button
        phase_log.append("click_submit")
        clicked = False
        btn_selectors = [
            "button:has-text('Submit')",
            "//button[contains(., 'Submit')]",
        ]
        for bsel in btn_selectors:
            try:
                page.click(bsel, timeout=3000)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            browser.close()
            return {
                "status": "error",
                "message": "Could not click the Submit button.",
                "phase_log": phase_log,
            }

        # Step 4: Wait for result page (/transcript/<id>)
        phase_log.append("wait_result_route")
        try:
            page.wait_for_url(re.compile(r"/transcript/"), timeout=120_000)
        except PWTimeout:
            browser.close()
            return {
                "status": "error",
                "message": "Did not navigate to /transcript/ page. Maybe bad link or rate limit.",
                "phase_log": phase_log,
            }

        # Step 5: Poll until 'Transcription completed' and a large transcript is present
        phase_log.append("poll_until_complete")
        deadline = time.time() + max_wait_sec
        transcript_text: Optional[str] = None
        completed_banner_seen = False

        while time.time() < deadline:
            # 5.a Wait for explicit completion banner (if present)
            if not completed_banner_seen:
                try:
                    page.wait_for_selector("text=Transcription completed", timeout=5_000)
                    completed_banner_seen = True
                except PWTimeout:
                    pass

            # 5.b Try to extract a big block of text
            if completed_banner_seen:
                transcript_text = _extract_big_text(page)
                if transcript_text:
                    break

            page.wait_for_timeout(1500)  # small idle wait

        browser.close()

        if not transcript_text:
            return {
                "status": "error",
                "message": "Timed out waiting for transcript. Try longer max_wait_sec or verify link.",
                "phase_log": phase_log,
            }

        return {
            "status": "ok",
            "source_url": url,
            "transcript": transcript_text,
            "phase_log": phase_log,
        }


# ---------- ROUTES ----------
@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Use POST /transcribe (JSON), POST /transcribe-form (form), GET /transcribe_q (query) or open /docs",
    }


@app.get("/health")
def health():
    return {"ok": True}


# JSON endpoint
@app.post("/transcribe")
def transcribe(req: TranscribeReq = Body(...)):
    phase_log: List[str] = []
    return _run_transcription(req.url, req.max_wait_sec, phase_log)


# Form endpoint (handy for n8n 'Form-URL Encoded')
@app.post("/transcribe-form")
def transcribe_form(
    url: str = Form(...),
    max_wait_sec: int = Form(600),
):
    phase_log: List[str] = []
    return _run_transcription(url, max_wait_sec, phase_log)


# GET endpoint (quick testing from a browser)
@app.get("/transcribe_q")
def transcribe_q(
    url: str = Query(...),
    max_wait_sec: int = Query(600),
):
    phase_log: List[str] = []
    return _run_transcription(url, max_wait_sec, phase_log)
