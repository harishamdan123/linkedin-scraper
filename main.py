from fastapi import FastAPI, Query
from pydantic import BaseModel
from typing import List, Dict, Optional
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import os, time, random

app = FastAPI(title="Indeed Easy Apply Scraper")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

def t(loc) -> str:
    try: return loc.first.inner_text().strip()
    except: return ""

def has_text(loc, needle: str) -> bool:
    try: return any(needle.lower() in (x or "").lower() for x in loc.all_inner_texts())
    except: return False

def indeed_login(ctx) -> None:
    """Login using INDEED_EMAIL / INDEED_PASSWORD env vars."""
    email = os.getenv("INDEED_EMAIL")
    password = os.getenv("INDEED_PASSWORD")
    if not email or not password:
        return  # run without login if not set

    page = ctx.new_page()
    # New login entry point
    page.goto("https://secure.indeed.com/", wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_timeout(1000)

    # Fill email
    try:
        page.wait_for_selector("input[type='email'], input[name='__email']", timeout=20_000)
        page.locator("input[type='email'], input[name='__email']").first.fill(email)
        page.locator("button[type='submit'], button:has-text('Continue')").first.click()
    except:
        pass

    # Fill password
    try:
        page.wait_for_selector("input[type='password'], input[name='__password']", timeout=20_000)
        page.locator("input[type='password'], input[name='__password']").first.fill(password)
        page.locator("button[type='submit'], button:has-text('Sign in')").first.click()
    except:
        pass

    # Basic Cloudflare checkbox attempt (best-effort)
    page.wait_for_timeout(2000)
    try:
        # common iframe title on CF pages
        cf_iframe = page.frame_locator("iframe[title*='security challenge'], iframe[title*='Cloudflare']")
        if cf_iframe:
            cf_iframe.locator("input[type='checkbox']").first.click(timeout=3000)
            page.wait_for_timeout(3000)
    except:
        pass

    # Done
    page.wait_for_timeout(1500)
    page.close()

def search_url(job_title: str, location: str) -> str:
    # sort=date to surface recent, you can tweak query params as needed
    return f"https://www.indeed.com/jobs?q={quote_plus(job_title)}&l={quote_plus(location)}&sort=date"

def scrape_indeed_easy(job_title: str, location: str, limit: int) -> List[Dict]:
    """Return ONLY 'Easily apply' jobs: role, company_name, link (Indeed job page)."""
    rows: List[Dict] = []
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900})
        ctx.set_default_navigation_timeout(120_000)
        ctx.set_default_timeout(60_000)

        # Login first if creds set
        indeed_login(ctx)

        page = ctx.new_page()
        url = search_url(job_title, location)
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_timeout(1500)

        def collect_from_current_page():
            nonlocal rows
            cards = page.locator("#mosaic-jobResults a.tapItem")  # job cards/links
            count = cards.count()
            for i in range(count):
                card = cards.nth(i)
                # Filter: must show "Easily apply"
                if not has_text(card, "Easily apply"):
                    continue

                link = card.get_attribute("href")
                if not link: continue
                if link.startswith("/"): link = "https://www.indeed.com" + link
                link = link.split("?")[0]
                if link in seen: continue
                seen.add(link)

                role = t(card.locator("h2.jobTitle span[title], h2.jobTitle span:not([class])"))
                if not role: role = t(card.locator("h2"))

                company = t(card.locator(".companyName"))
                if not company:
                    company = t(card.locator("span:has(+ .companyLocation)"))

                rows.append({"role": role, "company_name": company, "link": link})
                if len(rows) >= limit:
                    return True
            return False

        # page 1
        collect_from_current_page()
        # paginate until limit
        for _ in range(20):
            if len(rows) >= limit: break
            # click Next if present
            try:
                next_btn = page.locator("a[aria-label='Next'], a[aria-label='Next Page']")
                if next_btn.count() == 0:
                    break
                next_btn.first.click()
                page.wait_for_load_state("domcontentloaded", timeout=120_000)
                page.wait_for_timeout(1200)
                if collect_from_current_page():
                    break
            except:
                break

        browser.close()
    return rows

# ---------- API ----------
class IndeedReq(BaseModel):
    job_title: str
    location: str
    limit: int = 25

@app.post("/indeed/easy")
def indeed_easy_post(req: IndeedReq):
    jobs = scrape_indeed_easy(req.job_title, req.location, req.limit)
    return {"count": len(jobs), "jobs": jobs}

@app.get("/indeed/easy")
def indeed_easy_get(
    job_title: str = Query(...),
    location: str = Query(...),
    limit: int = Query(25)
):
    jobs = scrape_indeed_easy(job_title, location, limit)
    return {"count": len(jobs), "jobs": jobs}

@app.get("/ping")
def ping():
    return {"ok": True}
