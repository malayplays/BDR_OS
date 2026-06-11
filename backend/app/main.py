import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from app.api.routes import router  # noqa: E402
from app.database import engine  # noqa: E402
from app.models import Base  # noqa: E402

app = FastAPI(title="BDR OS", version="0.1.0")

app.include_router(router)

# DEBUG ONLY — mount simulation endpoints and /debug dashboard when DEBUG_DASHBOARD=true
_templates_dir = Path(__file__).resolve().parent / "templates"
_templates = Jinja2Templates(directory=str(_templates_dir))


if os.getenv("DEBUG_DASHBOARD", "").lower() == "true":
    from app.api.debug_sim import router as debug_sim_router  # noqa: E402

    app.include_router(debug_sim_router)

    @app.get("/debug", response_class=HTMLResponse)
    def debug_dashboard(request: Request):
        return _templates.TemplateResponse(request=request, name="debug.html")


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    _seed_fixture_jobs()


def _seed_fixture_jobs():
    """Seed jobs from fixtures if DB is empty."""
    import json
    from pathlib import Path

    from app.database import SessionLocal
    from app.models.job import Job

    db = SessionLocal()
    try:
        if db.query(Job).count() > 0:
            return
        fixtures_path = Path(__file__).resolve().parent.parent / "fixtures" / "crm.json"
        if not fixtures_path.exists():
            return
        data = json.loads(fixtures_path.read_text())
        for acct in data.get("accounts", [])[:5]:
            job = Job(
                job_type="research_brief",
                funnel_stage="create",
                agent="research_brief",
                trigger={"kind": "manual", "ref": acct["ref"]},
                account_ref=acct["ref"],
                status="pending",
                expected_value=0.024,
                priority_score=0.5,
            )
            db.add(job)
        db.commit()
    finally:
        db.close()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
