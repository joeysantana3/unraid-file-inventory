FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    rsync \
    htop \
    iotop \
    sysstat \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the scanner script
COPY nas_scanner_hp.py ./

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Optimize Python for performance
ENV PYTHONOPTIMIZE=1

CMD ["python", "nas_scanner_hp.py"] 