FROM python:3.12-slim

# git is the only system dependency - it is how repositories are fetched.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The analysis engine. Published separately on PyPI; pinned here so a bad
# release upstream cannot silently change what the service reports.
RUN pip install --no-cache-dir "falsegreen==0.1.0"

COPY app ./app
COPY static ./static

# Run unprivileged. The service clones untrusted repositories, and although it
# never executes their contents, there is no reason for it to hold root.
RUN useradd --create-home --uid 10001 scanner \
 && mkdir -p /srv/data \
 && chown -R scanner:scanner /srv
USER scanner

ENV FALSEGREEN_DB=/srv/data/falsegreen.db \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
