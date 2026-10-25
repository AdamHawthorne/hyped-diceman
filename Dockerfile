FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rollf.py .
COPY cogs ./cogs
COPY assets ./assets
COPY web ./web

RUN useradd --create-home --uid 1000 diceman \
    && chown -R diceman:diceman /app

USER diceman

CMD ["python", "rollf.py"]