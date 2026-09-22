# ... existing imports ...
from .clusters import router as clusters_router

app = FastAPI()

# ... existing router includes ...

app.include_router(clusters_router, prefix="/api")
