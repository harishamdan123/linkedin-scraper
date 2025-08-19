from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict

app = FastAPI()

class JobRequest(BaseModel):
    site: str
    apply_type: str
    job_title: str
    location: str
    limit: int

@app.post("/scrape")
async def scrape_jobs(req: JobRequest) -> Dict:
    site = req.site.lower()
    results = []

    if site == "linkedin":
        if req.apply_type == "easy":
            results = [{"role": "Data Scientist", "company_name": "LinkedIn Corp", "link": "https://linkedin.com/easyapply-example"}]
        else:
            results = [{"role": "Data Scientist", "company_name": "LinkedIn Corp", "link": "https://company.com/apply"}]

    elif site == "indeed":
        results = [{"role": "Data Analyst", "company_name": "Indeed Inc", "link": "https://indeed.com/apply"}]

    elif site == "glassdoor":
        results = [{"role": "ML Engineer", "company_name": "Glassdoor Ltd", "link": "https://glassdoor.com/apply"}]

    elif site == "ziprecruiter":
        results = [{"role": "Software Engineer", "company_name": "ZipRecruiter", "link": "https://ziprecruiter.com/apply"}]

    elif site == "monster":
        results = [{"role": "Backend Dev", "company_name": "Monster.com", "link": "https://monster.com/apply"}]

    else:
        return {"error": "site not supported"}

    return {"jobs": results}
