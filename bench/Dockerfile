# Portal accuracy runner. Inference happens on the target, scoring in the Job.
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch==2.8.0"
RUN pip install --no-cache-dir "lm-eval[api]==0.4.4" "transformers<5" "datasets<4"
COPY entrypoint.py accuracy_model.py /app/
ENTRYPOINT ["python", "/app/entrypoint.py"]
