FROM python:3.11-slim

# Installer wget
RUN apt-get update && apt-get install -y wget && apt-get clean

# Créer un répertoire de travail
WORKDIR /app

# Copier uniquement les dépendances d'abord
COPY requirements.txt .
RUN pip install -r requirements.txt

# Exposer le port Flask
EXPOSE 80

# Lancer l'app avec le rechargement automatique
CMD ["python", "app.py"]
