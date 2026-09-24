FROM python:3.12-slim

WORKDIR /app

# Le dipendenze in un layer separato: cambiano molto meno del codice.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY evaluation ./evaluation
COPY tests ./tests
COPY notebooks ./notebooks

# Il loader importa `app.graph`: la radice del progetto deve stare sul path.
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
