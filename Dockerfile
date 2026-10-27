FROM python:3.13-slim

# Sem .pyc no disco e com o log saindo na hora (sem buffer).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# As dependências primeiro: mudar só o código não reinstala tudo.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

# Sem root: se alguém achar uma falha no app, não ganha o container.
RUN useradd --create-home --uid 1000 monitor
USER monitor

EXPOSE 8000

# Atualiza o banco e sobe a API. O Render informa a porta em $PORT.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
