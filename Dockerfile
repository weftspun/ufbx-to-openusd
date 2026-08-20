# ufbx is fetched, not vendored: the version is a build argument rather than a copy
# nobody updates. Pinned to a tag so a rebuild is reproducible.
ARG UFBX_REF=v0.20.0

FROM python:3.12-slim AS build
ARG UFBX_REF
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -sLo ufbx.h "https://raw.githubusercontent.com/ufbx/ufbx/${UFBX_REF}/ufbx.h" \
 && curl -sLo ufbx.c "https://raw.githubusercontent.com/ufbx/ufbx/${UFBX_REF}/ufbx.c"
COPY probe.c .
RUN gcc -O2 -o probe probe.c ufbx.c -lm

FROM python:3.12-slim AS contract
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn usd-core
COPY --from=build /src/probe /usr/local/bin/probe
COPY server.py README.md ./
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
