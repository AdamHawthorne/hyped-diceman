FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rollf.py .
COPY assets ./assets
COPY web ./web

RUN useradd --create-home --uid 1000 rollf \
    && chown -R rollf:rollf /app

USER rollf

CMD ["python", "rollf.py"]