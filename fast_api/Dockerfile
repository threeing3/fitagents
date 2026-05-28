FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY ./requirements.txt ./requirements.txt
COPY app ./fast_api/app/

RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r ./requirements.txt

EXPOSE 8000

ENV PYTHONUNBUFFERED=True
ENV PYTHONPATH=/app

CMD ["uvicorn", "fast_api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
