FROM ghcr.io/xtls/xray-core:26.10.31 AS xray
FROM python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray
COPY --from=xray /usr/local/share/xray /usr/local/share/xray
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY router /app/router
ENV HOPWORLD_DATA=/data
VOLUME /data
EXPOSE 8080
CMD ["python", "-m", "router.server"]
