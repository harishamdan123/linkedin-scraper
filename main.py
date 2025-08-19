from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Optional
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import time, random

app = FastAPI(title="Job Scraper")

# ----- Input model (matches your /docs) -----
class JobRequest(BaseModel):
    site: str                 # linkedin | indeed | glassdoor | ziprecruiter | monster
    apply_type: str           # "easy" or "no_easy_apply"
    job_title: str
    location: str
    limit: int = 20

# ---------- Helpers ----------
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

def txt(loc) -> str:
    try:
        return loc.first.inner_text().strip()
    except:
        return ""

def has_txt(loc, needle: str) -> bool:
    try:
        return any(needle.lower() in (t or "").lower() for t in loc.all_inner_texts())
    except:
        return False

# ---------- LinkedIn ----------
def li_search_url(job_title: str, location: str, want_easy: bool) -> str:
    base = "https://www.linkedin.com/jobs/search/"
    url = f"{base}?keywords={quote_plus(job_title)}&location={quote_plus(location)}"
    if want_easy:
        url += "&f_AL=true"  # Easy Apply filter (we still verify on card)
    return url

def li_external_apply(job_page) -> Optional[str]:
    # common selectors for "Apply on company website"
    for sel in [
        "a[data-control-name='jobdetails_topcard_inapply']",
        "a[href*='offsiteapply']",
        "a:has-text('Apply on company website')",
        "a:has-text('Apply on company site')",
    ]:
        try:
            href = job_page.locator(sel).first.get_attribute("href")
            if href:
                return href
        except:
            pass
    return None

def scrape_linkedin(job_title: str, location: str, limit: int, apply_type: str) -> List[Dict]:
    want_easy = (apply_type == "easy")
    url = li_search_url(job_title, location, want_easy)

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

        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_timeout(1500)
        try:
            page.wait_for_selector("ul.jobs-search__results-list li", timeout=20_000)
        except PWTimeout:
            pass

        last = 0
        stable = 0

        for _ in range(60):  # ~60 scroll passes
            cards = page.locator("ul.jobs-search__results-list li")
            cnt = cards.count()

            for i in range(cnt):
                card = cards.nth(i)

                # check Easy Apply badge
                is_easy = has_txt(card.locator("span"), "Easy Apply")
                if want_easy and not is_easy:
                    continue
                if (not want_easy) and is_easy:
                    continue

                role = txt(card.locator("h3.base-search-card__title")) or txt(card.locator("h3"))
                company = (txt(card.locator("h4.base-search-card__subtitle a"))
                           or txt(card.locator("h4.base-search-card__subtitle"))
                           or txt(card.locator("a.hidden-nested-link")))

                job_link = None
                try:
                    job_link = card.locator("a.base-card__full-link").first.get_attribute("href")
                except:
                    try:
                        job_link = card.locator("a").first.get_attribute("href")
                    except:
                        job_link = None
                if not job_link:
                    continue
                job_link = job_link.split("?")[0]
                if job_link in seen:
                    continue
                seen.add(job_link)

                if want_easy:
                    # return LinkedIn job page (with Easy Apply)
                    rows.append({"role": role, "company_name": company, "link": job_link})
                else:
                    # open job page → get external company apply URL
                    apply_url = None
                    try:
                        jp = ctx.new_page()
                        jp.goto(job_link, wait_until="domcontentloaded", timeout=60_000)
                        jp.wait_for_timeout(1200)
                        apply_url = li_external_apply(jp)
                        jp.close()
                    except:
                        apply_url = None

                    if apply_url:
                        rows.append({"role": role, "company_name": company, "link": apply_url})
                    # if no external link, skip (only-no-easy-apply requested)

                if len(rows) >= limit:
                    browser.close()
                    return rows

            # stop if not finding new ones
            if len(rows) == last:
                stable += 1
            else:
                stable = 0
                last = len(rows)
            if stable >= 4:
                break

            # gentle scroll
            page.mouse.wheel(0, 2500)
            time.sleep(random.uniform(0.8, 1.8))

        browser.close()
    return rows

# ---------- Other sites (placeholders to fill later) ----------
def scrape_indeed(*args, **kwargs):
    return [{"note":"Indeed login/captcha flow to be added (Smart Apply vs company apply)"}]

def scrape_glassdoor(*args, **kwargs):
    return [{"note":"Glassdoor login flow to be added (EasyApplySender vs employer site)"}]

def scrape_zip(*args, **kwargs):
    return [{"note":"ZipRecruiter flow to be added (1-Click vs employer site)"}]

def scrape_monster(*args, **kwargs):
    return [{"note":"Monster flow to be added (Easy Apply vs employer site)"}]

# ---------- API ----------
@app.post("/scrape")
def scrape(req: JobRequest):
    site = req.site.lower().strip()
    apply_type = req.apply_type.lower().strip()

    if site not in {"linkedin","indeed","glassdoor","ziprecruiter","monster"}:
        return {"error":"site must be one of: linkedin, indeed, glassdoor, ziprecruiter, monster"}
    if apply_type not in {"easy","no_easy_apply"}:
        return {"error":"apply_type must be 'easy' or 'no_easy_apply'"}

    if site == "linkedin":
        jobs = scrape_linkedin(req.job_title, req.location, req.limit, apply_type)
    elif site == "indeed":
        jobs = scrape_indeed(req.job_title, req.location, req.limit, apply_type)
    elif site == "glassdoor":
        jobs = scrape_glassdoor(req.job_title, req.location, req.limit, apply_type)
    elif site == "ziprecruiter":
        jobs = scrape_zip(req.job_title, req.location, req.limit, apply_type)
    else:
        jobs = scrape_monster(req.job_title, req.location, req.limit, apply_type)

    return {"count": len(jobs), "jobs": jobs}

@app.get("/ping")
def ping():
    return {"ok": True}
