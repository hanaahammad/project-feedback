from fastapi import FastAPI

app = FastAPI(title="Project Feedback")


@app.get("/")
def home() -> dict[str, str]:
    return {"status": "ok"}
