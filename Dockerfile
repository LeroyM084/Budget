FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Paris \
    DATABASE_PATH=/data/enveloppes.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

COPY . .

# Utilisateur sans privilèges, propriétaire du dossier des données. UID fixe :
# un volume existant reste accessible après reconstruction de l'image.
RUN useradd --uid 10001 --user-group --no-create-home enveloppes \
    && mkdir /data \
    && chown enveloppes:enveloppes /data
USER enveloppes
VOLUME /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/sante', timeout=3)"]

# --no-control-socket : sinon Gunicorn 26 crée une socket d'administration dans $HOME, absent ici.
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8000", "--no-control-socket", "app:create_app()"]
