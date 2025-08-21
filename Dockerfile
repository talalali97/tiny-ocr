FROM python:3.11-slim

# System deps for tesseract, ocrmypdf, poppler
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng \
    ocrmypdf ghostscript qpdf \
    poppler-utils \
    libgl1 libglib2.0-0 \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
