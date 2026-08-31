# L'agent ABO. Volontairement minuscule : il ne porte aucun modele, aucun poids
# et aucune dependance de calcul. Ce qui calcule vit dans les conteneurs de
# moteurs, joignables sur le reseau interne.
FROM python:3.12-slim

WORKDIR /srv

RUN pip install --no-cache-dir "httpx>=0.27,<1"

COPY agent.py .

# Aucun port expose : tout part de la machine vers ABO.
CMD ["python", "-u", "agent.py"]
