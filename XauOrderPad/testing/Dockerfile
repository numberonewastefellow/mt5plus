# Headless test image for the XauOrderPad API. TEST ONLY -- see README.md.
#
# The real requirements.txt cannot be used as-is: its first line is
# `MetaTrader5>=5.0.4424`, which has NO Linux wheel. We install everything else, plus the
# fake MetaTrader5 module (stub/), which is the only thing standing between this container
# and the real, unmodified server.py.
#
# numpy is listed EXPLICITLY on purpose. strategy.py:27 imports it, but it is absent from
# requirements.txt -- today it only arrives as a transitive dependency of MetaTrader5.
# Drop MetaTrader5 and numpy silently disappears with it, and `import server` dies. That
# is a latent packaging bug in the real requirements.txt, not just a test-image quirk.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir \
        "fastapi>=0.110" \
        "uvicorn[standard]>=0.29" \
        "numpy" \
        "keyring" "keyrings.alt" \
        "pytest" "pytest-asyncio" "httpx" "websockets"

# The stub goes FIRST on PYTHONPATH so `import MetaTrader5` resolves to the fake one.
# It refuses to load unless XAUORDERPAD_FAKE_MT5=1 (set in docker-compose.yml).
ENV PYTHONPATH=/app/testing/stub:/app

CMD ["sleep", "infinity"]
