FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY web ./web
COPY config ./config
RUN pip install --no-cache-dir -e .
RUN mkdir -p /app/data/media /app/data/exports

EXPOSE 8000
CMD ["uvicorn", "xhs_workflow.main:app", "--host", "0.0.0.0", "--port", "8000"]
