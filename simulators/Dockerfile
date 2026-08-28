FROM python:3.12-slim

WORKDIR /app

COPY sdk/python/ /app/sdk/python/
COPY simulators/ /app/simulators/
COPY drivers/ /app/drivers/

RUN pip install --no-cache-dir -e /app/sdk/python/ 2>/dev/null || true

ENV PYTHONPATH=/app

CMD ["python", "-m", "simulators.universal_robots"]
