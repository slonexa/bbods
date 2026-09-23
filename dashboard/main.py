from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from engine.db import Database
import os

app = FastAPI()
db = Database()

# Setup static and media files directories
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

media_dir = os.path.join(os.path.dirname(__file__), "..", "media")
if os.path.exists(media_dir):
    app.mount("/media", StaticFiles(directory=media_dir), name="media")

@app.get("/")
def read_root():
    return FileResponse(os.path.join(static_dir, "index.html"))

@app.get("/api/spreads")
def get_spreads():
    # Returns latest clean deduplicated live market strikes (1 row per strike)
    spreads = db.get_latest_clean_spreads(limit=50)
    return {"data": spreads}

@app.get("/api/raw_spreads")
def get_raw_spreads():
    # Returns raw chronological tick stream (including all ABOVE/BELOW ticks) for debugging
    raw = db.get_recent_spreads(limit=100)
    return {"data": raw}

@app.get("/api/top_spreads")
def get_top_spreads():
    # Returns the highest spreads recorded
    top = db.get_top_spreads(limit=15)
    return {"data": top}
