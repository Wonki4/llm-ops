FROM python:3.11-slim

WORKDIR /app

# Copy everything then install (editable so 'app' is importable from /app)
COPY . .
RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
