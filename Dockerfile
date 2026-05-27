# Usa uma imagem oficial do Python, leve e rápida
FROM python:3.10-slim

# Define a pasta de trabalho dentro do container
WORKDIR /app

# Copia as dependências primeiro para aproveitar o cache do Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o resto do seu projeto para o container
COPY . .

# Expõe a porta que o Uvicorn vai rodar (padrão 8000)
EXPOSE 8000

# Comando para rodar sua aplicação
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
