from fastapi import FastAPI, Query
from pydantic import BaseModel
from typing import Optional, Dict, List
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import random, time

app = FastAPI(title="Multi-site Job Scraper")

# ---------- Helpers ----------
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

def get_text_safe(locator) -> str:
    try:
        return locator.first.inner_text().strip()
    except:
        return ""

def has_text(locator, needle: str) -> bool:
    try:
        texts = locator.all_inner_texts()
        return any(needle.lower() in (t or "").lower() for t in texts)
    except:
        return False

# ---------- LinkedIn ----------
def li_build_url(job_title: str, location: str, want_easy: bool) -> str:
    base = "https://www.linkedin.com/jobs/search/"
    url = f"{base}?keywords={quote_plus(job_title)}&location={quote_plus(location)}"
    if want_easy:
        url += "&f_AL=true"  # Easy Apply filter (we still verify on page)
    return url

def li_find_external_apply(job_page) -> Optional[str]:
    # Try a few known selectors for "Apply on company website"
    candidates = [
        "a[data-control-name='jobdetails_topcard_inapply']",
        "a[href*='offsiteapply']",
        "a:has-text('Apply on company website')",
        "a:has-text('Apply on company site')",
    ]
    for sel in candidates:
        try:
            link = job_page.locator(sel).first.get_attribute("href")
            if link:
                return link
        except:
            pass
    return None

def scrape_linkedin(job_title: str, location: str, limit: int, apply_type: str) -> List[Dict]:
    """
    apply_type:
      - 'easy'    -> ONLY Easy Apply; return LinkedIn job link
      - 'company' -> ONLY non-Easy Apply; return external company apply link
    """
    want_easy = (apply_type == "easy")
    url = li_build_url(job_title, location, want_easy)

    results: List[Dict] = []
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        try:
            page.wait_for_selector("ul.jobs-search__results-list li", timeout=10_000)
        except PWTimeout:
            pass

        last_count = 0
        stable_loops = 0

        for _ in range(60):  # up to ~60 scroll passes
            cards = page.locator("ul.jobs-search__results-list li")
            count = cards.count()

            for i in range(count):
                card = cards.nth(i)

                # detect Easy Apply badge on the card
                is_easy = has_text(card.locator("span"), "Easy Apply")

                # filter by apply_type
                if want_easy and not is_easy:
                    continue
                if not want_easy and is_easy:
                    continue

                role = (get_text_safe(card.locator("h3.base-search-card__title"))
                        or get_text_safe(card.locator("h3")))
                company = (get_text_safe(card.locator("h4.base-search-card__subtitle a"))
                           or get_text_safe(card.locator("h4.base-search-card__subtitle"))
                           or get_text_safe(card.locator("a.hidden-nested-link")))

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
                    # Return LinkedIn job page where Easy Apply is present
                    results.append({"company": company, "role": role, "link": job_link})
                else:
                    # Open job page and extract the company-site apply link
                    apply_link = None
                    try:
                        job_page = ctx.new_page()
                        job_page.goto(job_link, wait_until="domcontentloaded", timeout=30_000)
                        job_page.wait_for_timeout(1200)
                        apply_link = li_find_external_apply(job_page)
                        job_page.close()
                    except:
                        apply_link = None

                    if apply_link:
                        results.append({"company": company, "role": role, "link": apply_link})
                    # If no external link, skip (you asked for ONLY company-apply here)

                if len(results) >= limit:
                    browser.close()
                    return results

            # if we stop discovering new items, exit
            if len(results) == last_count:
                stable_loops += 1
            else:
                stable_loops = 0
                last_count = len(results)
            if stable_loops >= 4:
                break

            # gentle scroll
            page.mouse.wheel(0, 2500)
            time.sleep(random.uniform(0.8, 1.8))

        browser.close()
    return results

# ---------- Other sites: stubs (to be wired with login/cookies) ----------
def scrape_indeed(job_title: str, location: str, limit: int, apply_type: str) -> List[Dict]:
    return [{"note": "Indeed login/captcha flow pending. Will return Smart Apply or company apply links accordingly."}]

def scrape_glassdoor(job_title: str, location: str, limit: int, apply_type: str) -> List[Dict]:
    return [{"note": "Glassdoor login pending. Will return EasyApplySender or employer-site links accordingly."}]

def scrape_zip(job_title: str, location: str, limit: int, apply_type: str) -> List[Dict]:
    return [{"note": "ZipRecruiter flow pending. Will return 1-Click Apply or employer-site links."}]

def scrape_monster(job_title: str, location: str, limit: int, apply_type: str) -> List[Dict]:
    return [{"note": "Monster flow pending. Will return Easy Apply or employer-site links."}]

# ---------- API models ----------
class ScrapeReq(BaseModel):
    platform: str          # linkedin | indeed | glassdoor | ziprecruiter | monster
    apply_type: str        # easy | company
    job_title: str
    location: str
    max_jobs: int = 50

def run_scrape(q: ScrapeReq) -> Dict:
    plat = q.platform.lower().strip()
    apply_type = q.apply_type.lower().strip()

    if plat not in {"linkedin", "indeed", "glassdoor", "ziprecruiter", "monster"}:
        return {"error": "platform must be one of: linkedin, indeed, glassdoor, ziprecruiter, monster"}
    if apply_type not in {"easy", "company"}:
        return {"error": "apply_type must be 'easy' or 'company'"}

    if plat == "linkedin":
        jobs = scrape_linkedin(q.job_title, q.location, q.max_jobs, apply_type)
    elif plat == "indeed":
        jobs = scrape_indeed(q.job_title, q.location, q.max_jobs, apply_type)
    elif plat == "glassdoor":
        jobs = scrape_glassdoor(q.job_title, q.location, q.max_jobs, apply_type)
    elif plat == "ziprecruiter":
        jobs = scrape_zip(q.job_title, q.location, q.max_jobs, apply_type)
    else:  # monster
        j
