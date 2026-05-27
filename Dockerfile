FROM python:3.10-slim

WORKDIR /app

# Instala apenas o necessário
RUN pip install --no-cache-dir fastapi uvicorn uvloop httpx jinja2

# Copia o seu app.py
COPY app.py .

# Expõe a porta 8000
EXPOSE 8000

# Comando para rodar garantindo que ele use a porta correta
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
