FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

# Modbus servers + control API
EXPOSE 5020-5119 8080

ENTRYPOINT ["fire-sim"]
CMD ["--buildings", "100", "--host", "0.0.0.0", "--base-port", "5020", "--api-port", "8080"]
