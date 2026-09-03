# Moteur ABO — ClearerVoice / MossFormer2, rehaussement 48 kHz.
#
#   docker build -t abo-clearervoice:dev engines/clearervoice
#
# Construit sur la base commune : torch y est deja et n'est pas reinstalle
# (`clearvoice` demande `torch>=2.0.1`, la base epingle 2.6.0).
#
# Les poids sont cuits dans l'image, sous le `COPY server.py` (`ADR-009` § 9).
# Ne pas monter de volume sur `/weights` : il les masquerait.
#
# Cette image tire `torchvision`, `opencv` et `scenedetect` : le paquet amont
# sait aussi separer des voix a partir d'une video, ce dont ABO ne se sert pas.
# On les subit parce qu'elles sont des dependances dures ; il n'y a pas de
# variante audio seule publiee.
# Le socle est reference par un ARG pour que la meme recette serve ici et
# ailleurs : en local `abo-engine-base:cuda` suffit, en CI on epingle le tag
# du commit courant pour que moteur et socle ne puissent pas diverger.
ARG BASE_IMAGE=antoninocanta/abo-engine-base:cuda
FROM ${BASE_IMAGE}

# `libgl1` et `libglib2.0-0` : opencv les charge au demarrage meme quand aucune
# image ne passe. Sans elles, l'import echoue avec une erreur de bibliotheque
# partagee qui ne dit pas de quoi il s'agit.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ARG CLEARVOICE_VERSION=0.1.2
RUN pip install --no-cache-dir "clearvoice==${CLEARVOICE_VERSION}"

# --- Les poids, cuits ici et pas ailleurs ------------------------------------
# Meme principe que Chatterbox : on instancie exactement comme `model()` le
# fera, donc les fichiers se posent la ou le serveur ira les lire — et cela
# vaut meme si l'amont n'ecrit pas dans `HF_HOME` mais dans un repertoire a
# lui, ce qu'on n'a pas a savoir.
#
# `MossFormer2_SE_48K` est le checkpoint par defaut du serveur. Le figer ici
# veut dire qu'un autre checkpoint demande par une route se telechargerait au
# premier appel : c'est voulu, on ne cuit que ce qu'on sert.
ARG CLEARVOICE_CHECKPOINT=MossFormer2_SE_48K
RUN python -c "from clearvoice import ClearVoice; ClearVoice(task='speech_enhancement', model_names=['${CLEARVOICE_CHECKPOINT}']); print('poids clearervoice en place')"

COPY server.py /opt/abo/server.py

EXPOSE 18100
CMD ["uvicorn", "server:app", "--app-dir", "/opt/abo", "--host", "0.0.0.0", "--port", "18100"]
