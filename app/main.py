from fastapi import FastAPI

from app.auth import router as auth_router
from app.example_protected import router as example_protected_router

app = FastAPI(title="Project Feedback")
app.include_router(auth_router)
app.include_router(example_protected_router)


@app.get("/")
def home() -> dict[str, str]:
    return {"status": "ok"}
