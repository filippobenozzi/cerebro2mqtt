FROM python:3.12-slim

WORKDIR /opt/cerebro2mqtt

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV CEREBRO_CONFIG=/data/config.json

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY templates ./templates

VOLUME ["/data"]

EXPOSE 80

CMD ["python", "-m", "app.main"]
