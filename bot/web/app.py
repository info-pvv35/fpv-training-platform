from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from ..database.db import fetch
from ..config import SCHEDULE_URL
import qrcode
from io import BytesIO
import base64

app = FastAPI(title="FPV Admin Panel")

app.mount("/static", StaticFiles(directory="bot/web/static"), name="static")
templates = Jinja2Templates(directory="bot/web/templates")

@app.get("/", response_class=HTMLResponse)
async def admin_panel(request: Request):
    trainings = await fetch('SELECT * FROM trainings ORDER BY date, time')
    return templates.TemplateResponse("admin.html", {"request": request, "trainings": trainings})