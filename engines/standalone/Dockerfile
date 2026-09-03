# Une image = un worker complet. Pour une machine louee.
#
# Pourquoi elle existe : la ferme ABO fait tourner l'agent et le moteur dans
# **deux conteneurs** sur un meme hote (`deploy/docker-compose.yml`). Un
# hebergeur a la demande, lui, lance **une seule image** par instance — il n'y a
# pas d'hote ou poser un compose. Une machine louee a donc besoin d'une image
# qui porte les deux, exactement comme `engines/qwen3_tts/entrypoint.sh` le fait
# deja pour le proxy PyWorker.
#
# C'est une couche **mince** posee sur un moteur deja construit : elle n'ajoute
# que l'agent, `httpx`, et un entrypoint qui demarre les deux. Les poids et le
# moteur ne sont pas dupliques — l'image de base est partagee avec la variante
# de ferme, donc une machine qui a deja l'une ne retelecharge presque rien.
#
#   docker build -f engines/standalone/Dockerfile \
#     --build-arg ENGINE_IMAGE=antoninocanta/abo-deepfilternet:v1 \
#     -t abo-deepfilternet:standalone .
#
# **Le contexte est la racine du depot** : `agent/agent.py` et `engines/` vivent
# dans deux branches differentes de l'arborescence, et un contexte place dans
# l'une ne voit pas l'autre.
ARG ENGINE_IMAGE
FROM ${ENGINE_IMAGE}

# L'agent ne depend que de `httpx`. Le moteur, lui, peut ne pas l'avoir :
# DeepFilterNet n'embarque que fastapi et uvicorn.
RUN pip install --no-cache-dir "httpx>=0.27,<1"

COPY agent/agent.py /opt/abo/agent.py
COPY engines/standalone/entrypoint.sh /opt/abo/entrypoint.sh
RUN chmod +x /opt/abo/entrypoint.sh

# Aucun port publie, et c'est le point : l'agent compose vers ABO **en
# sortant**, le moteur n'ecoute que sur la boucle locale. Une instance louee n'a
# donc besoin d'aucune configuration reseau, d'aucun VPN et d'aucune adresse
# publique — c'est ce qui rend n'importe quel hebergeur utilisable.
#
# `CMD []` efface celui du moteur : sans cela il arriverait en arguments de
# l'entrypoint.
ENTRYPOINT ["/opt/abo/entrypoint.sh"]
CMD []
