from fastapi import FastAPI
from pydantic import BaseModel
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from urllib.parse import quote_plus
import random, time

app = FastAPI()

class JobReq(BaseModel):
    job_title: str       # e.g. "Data Scientist"
    easy_apply: bool     # True/False (filters only)
    location: str        # e.g. "New York"
    max_jobs: int = 50   # e.g. 100

def build_url(job_title: str, location: str, easy_apply: bool) -> str:
    url = (
        "https://www.linkedin.com/jobs/search/"
        f"?keywords={quote_plus(job_title)}"
        f"&location={quote_plus(location)}"
    )
    if easy_apply:
        url += "&f_AL=true"  # Easy Apply filter
    return url

def get_text_safe(locator):
    try:
        return locator.first.inner_text().strip()
    except:
        return ""

def scrape_once(job_title: str, easy_apply: bool, location: str, max_jobs: int):
    url = build_url(job_title, location, easy_apply)
    results = []
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        try:
            page.wait_for_selector("ul.jobs-search__results-list li", timeout=10_000)
        except PWTimeout:
            pass

        stable_loops = 0
        last_count = 0

        for _ in range(60):
            cards = page.locator("ul.jobs-search__results-list li")
            count = cards.count()

            for i in range(count):
                card = cards.nth(i)
                title = get_text_safe(card.locator("h3.base-search-card__title")) or get_text_safe(card.locator("h3"))
                company = get_text_safe(card.locator("h4.base-search-card__subtitle a")) or \
                          get_text_safe(card.locator("h4.base-search-card__subtitle")) or \
                          get_text_safe(card.locator("a.hidden-nested-link"))

                link = None
                try:
                    link = card.locator("a.base-card__full-link").first.get_attribute("href")
                except:
                    try:
                        link = card.locator("a").first.get_attribute("href")
                    except:
                        pass

                if link:
                    link = link.split("?")[0]
                    if link not in seen:
                        seen.add(link)

                        ### NEW: open job page to check Easy Apply vs Company site
                        job_page = ctx.new_page()
                        job_page.goto(link, wait_until="domcontentloaded", timeout=30_000)

                        job_type = "Unknown"
                        company_site = None

                        try:
                            # If Easy Apply button exists
                            if job_page.locator("button.jobs-apply-button").is_visible():
                                job_type = "Easy Apply"
                            else:
                                # Try company site apply
                                job_page.wait_for_selector("a[href*='companyWebsiteApply']", timeout=5000)
                                company_site_btn = job_page.locator("a[href*='companyWebsiteApply']").first
                                company_site_btn.click()

                                # handle popup "Continue without sign in"
                                try:
                                    job_page.wait_for_selector("button[aria-label*='Continue without']", timeout=5000)
                                    job_page.locator("button[aria-label*='Continue without']").click()
                                except:
                                    pass

                                # after click, new page opens
                                for popup in ctx.pages:
                                    if popup != job_page:
                                        company_site = popup.url
                                        break
                                job_type = "Company Site"
                        except:
                            pass

                        results.append({
                            "company": company,
                            "role": title,
                            "link": link,
                            "apply_type": job_type,
                            "company_site": company_site
                        })

                        job_page.close()

                        if len(results) >= max_jobs:
                            browser.close()
                            return results

            if len(results) == last_count:
                stable_loops += 1
            else:
                stable_loops = 0
                last_count = len(results)
            if stable_loops >= 4:
                break

            page.mouse.wheel(0, 2500)
            time.sleep(random.uniform(0.8, 1.8))

        browser.close()
    return results

@app.post("/scrape")
def scrape(req: JobReq):
    jobs = scrape_once(req.job_title, req.easy_apply, req.location, req.max_jobs)
    return {"count": len(jobs), "jobs": jobs}
