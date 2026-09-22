from fastapi import FastAPI

from app.auth import router as auth_router
from app.cards import router as cards_router
from app.cycles import router as cycles_router
from app.example_protected import router as example_protected_router
from app.projects import router as projects_router

app = FastAPI(title="Project Feedback")
app.include_router(auth_router)
app.include_router(example_protected_router)
app.include_router(projects_router)
app.include_router(cycles_router)
app.include_router(cards_router)


@app.get("/")
def home() -> dict[str, str]:
    return {"status": "ok"}
