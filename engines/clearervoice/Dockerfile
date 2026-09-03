# Moteur ABO — ClearerVoice / MossFormer2, rehaussement 48 kHz.
#
#   docker build -t abo-clearervoice:dev engines/clearervoice
#
# Construit sur la base commune : torch y est deja et n'est pas reinstalle
# (`clearvoice` demande `torch>=2.0.1`, la base epingle 2.6.0).
#
# Les poids se telechargent au premier appel dans `HF_HOME`, monte depuis le
# disque de travail — jamais cuits dans l'image.
#
# Cette image tire `torchvision`, `opencv` et `scenedetect` : le paquet amont
# sait aussi separer des voix a partir d'une video, ce dont ABO ne se sert pas.
# On les subit parce qu'elles sont des dependances dures ; il n'y a pas de
# variante audio seule publiee.
FROM abo-engine-base:cuda

# `libgl1` et `libglib2.0-0` : opencv les charge au demarrage meme quand aucune
# image ne passe. Sans elles, l'import echoue avec une erreur de bibliotheque
# partagee qui ne dit pas de quoi il s'agit.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ARG CLEARVOICE_VERSION=0.1.2
RUN pip install --no-cache-dir "clearvoice==${CLEARVOICE_VERSION}"

COPY server.py /opt/abo/server.py

EXPOSE 18100
CMD ["uvicorn", "server:app", "--app-dir", "/opt/abo", "--host", "0.0.0.0", "--port", "18100"]
