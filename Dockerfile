# filmatch is standard library only, so there is nothing to pip install.
FROM python:3.13-slim

WORKDIR /app
COPY filmatch.py ./
COPY web/ ./web/

# The filamentcolors.xyz swatch library is cached under XDG_CACHE_HOME. Pointing
# it at the data volume means a restart reuses the cache instead of spending a
# minute re-downloading ~2300 swatches.
ENV XDG_CACHE_HOME=/data/cache \
    PYTHONUNBUFFERED=1

RUN useradd --uid 1000 --create-home filmatch \
    && mkdir -p /data/cache \
    && chown -R filmatch:filmatch /data
USER filmatch

VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/', timeout=4)"

# Flags appended by `docker run` / compose `command:` land after these, and
# argparse lets a later flag win -- so `--spools /data/other.json` or
# `--suggest` can be added without repeating the whole line.
ENTRYPOINT ["python3", "/app/filmatch.py", "--serve", "--no-browser", \
            "--host", "0.0.0.0", "--port", "8765", "--spools", "/data/my-spools.json"]
