FROM python:3.11-slim
WORKDIR /app
COPY web/requirements.txt ./
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "bot.web.app:app", "--host", "0.0.0.0", "--port", "8000"]