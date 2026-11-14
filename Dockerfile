FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY router /app/router
ENV HOPWORLD_DATA=/data
VOLUME /data
EXPOSE 8080
CMD ["python", "-m", "router.server"]
