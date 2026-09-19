FROM python:3.12-slim

# git is the only system dependency - it is how repositories are fetched.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The analysis engine, installed from the repository that actually contains it
# and pinned to an exact commit.
#
# Deliberately NOT from PyPI. The name `falsegreen` on PyPI belongs to an
# unrelated project by a different author (github.com/vinicq/falsegreen); its
# package ships scanner.py and hook_install.py and has no cli, detectors,
# models or score modules. `pip install falsegreen==0.1.0` therefore installed
# a stranger's code into this image while providing none of the imports
# app/scanner.py needs - the container could not have started. A commit SHA is
# used rather than a tag or branch because only the SHA is immutable.
RUN pip install --no-cache-dir     "falsegreen @ git+https://github.com/Prasad-PingFederate/falsegreen@90013e44b203cd6a10495db73b7378a2b7d48b2a"

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
