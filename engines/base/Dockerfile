# Base commune aux moteurs qui ont besoin de torch.
#
# Elle existe pour une raison de place, et pas d'elegance : torch avec ses
# bibliotheques CUDA pese plusieurs gigaoctets, et trois moteurs qui
# l'installeraient chacun de leur cote le stockeraient trois fois. Construits
# sur cette base, ils partagent la couche : torch est ecrit une fois.
#
# Pas d'image `nvidia/cuda` en dessous : les roues torde portent elles-memes
# le runtime CUDA dont elles ont besoin. Seul le pilote vient de l'hote. Cela
# economise l'image de base CUDA, soit deux a trois gigaoctets pour rien.
#
# Python 3.11 et non 3.12 : plusieurs des paquets audio vises ne publient pas
# encore de roues pour 3.12, et compiler depuis les sources sur une machine
# d'utilisateur est exactement ce qu'on cherche a eviter.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # Les poids ne vivent PAS dans l'image. Ils sont montes depuis le disque de
    # travail : une image qui les porte se retelecharge en entier a chaque
    # correction de code, et remplit le disque de la machine qui la construit.
    # Qwen fait l'inverse a dessein — sur une instance louee reveillee a froid,
    # retelecharger plusieurs gigaoctets coute plus cher que le stockage. Sur
    # un PC qu'on possede, cette raison tombe.
    HF_HOME=/weights \
    TORCH_HOME=/weights/torch \
    ENGINE_PORT=18100

# `ffmpeg` et `libsndfile1` : les paquets audio les appellent pour lire autre
# chose que du WAV. `git` : certaines dependances s'installent depuis un depot.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg libsndfile1 git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Version epinglee : une version de modele encore `SERVING` doit rester
# relancable a l'identique (`ADR-004`). Un torch qui glisse changerait le son
# rendu sans que rien ne le dise.
ARG TORCH_VERSION=2.6.0
ARG CUDA_TAG=cu124
RUN pip install --no-cache-dir \
      --index-url "https://download.pytorch.org/whl/${CUDA_TAG}" \
      "torch==${TORCH_VERSION}" "torchaudio==${TORCH_VERSION}"

# Le serveur HTTP de tous les moteurs. Meme contrat, meme dependances.
RUN pip install --no-cache-dir fastapi==0.115.6 uvicorn==0.34.0 soundfile==0.13.1

# Normalisation de l'entree et verification du contenu rendu : la regle qui a
# ete payee quatre fois sur ce projet vit dans un seul fichier, pas dans trois
# copies qui divergeront.
COPY aboengine.py /opt/abo/aboengine.py

WORKDIR /opt/abo
