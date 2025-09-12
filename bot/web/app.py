from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from ..database.db import fetch, init_db_pool
from ..config import SCHEDULE_URL
import qrcode
from io import BytesIO
import base64

app = FastAPI(title="FPV Admin Panel")

# Инициализация пула БД при старте веб-приложения
@app.on_event("startup")
async def on_startup():
    await init_db_pool()
    print("✅ Web: Database pool initialized")

@app.on_event("shutdown")
async def on_shutdown():
    from ..database.db import close_db_pool
    await close_db_pool()
    print("✅ Web: Database pool closed")

app.mount("/static", StaticFiles(directory="bot/web/static"), name="static")
templates = Jinja2Templates(directory="bot/web/templates")

@app.get("/", response_class=HTMLResponse)
async def admin_panel(request: Request):
    trainings = await fetch('SELECT * FROM trainings ORDER BY date, time')
    return templates.TemplateResponse("admin.html", {"request": request, "trainings": trainings})