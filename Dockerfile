FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 doctor
COPY pyproject.toml alembic.ini ./
COPY migrations ./migrations
COPY personal_doctor ./personal_doctor
RUN mkdir -p /app/data && chown -R doctor:doctor /app
USER doctor
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["python", "-m", "personal_doctor", "serve", "--host", "0.0.0.0", "--port", "8000"]
