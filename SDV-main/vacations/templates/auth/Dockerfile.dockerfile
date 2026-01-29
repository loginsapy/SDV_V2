# Usar una imagen base de Python
FROM python:3.11-slim

# Establecer el directorio de trabajo
WORKDIR /app

# Copiar los archivos de requerimientos e instalarlos
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto de la aplicación
COPY . .

# Exponer el puerto en el que Gunicorn se ejecutará
EXPOSE 5001

# Comando para ejecutar la aplicación con Gunicorn
# Se asume que en vacations/__init__.py, la variable 'app' se crea con create_app()
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "run:app"]