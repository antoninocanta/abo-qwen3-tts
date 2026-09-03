# Moteur ABO — Resemble Enhance, nettoyage profond (`AUDIO_ENHANCE`).
#
#   docker build -t abo-resemble-enhance:dev engines/resemble_enhance
#
# **Ce moteur ne partage pas la couche torch des deux autres**, et il faut le
# dire plutôt que le découvrir : l'amont épingle `torch==2.1.1` là où la base
# est en 2.6. Installer par-dessus la base masque sa version au lieu de la
# remplacer — les fichiers de 2.6 restent en dessous, sans être chargés. On y
# gagne quand même : la base porte ffmpeg, libsndfile, le serveur HTTP et
# `aboengine`, et sa couche torch est déjà stockée pour ClearerVoice et
# Chatterbox, donc la réutiliser ne coûte pas d'octets neufs.
#
# Les poids sont cuits dans l'image, sous le `COPY server.py` (`ADR-009` § 9).
# Ne pas monter de volume sur `/weights` : il les masquerait.
# Le socle est reference par un ARG pour que la meme recette serve ici et
# ailleurs : en local `abo-engine-base:cuda` suffit, en CI on epingle le tag
# du commit courant pour que moteur et socle ne puissent pas diverger.
ARG BASE_IMAGE=antoninocanta/abo-engine-base:cuda
FROM ${BASE_IMAGE}

# `git-lfs`, et pas seulement `git` : l'amont ne passe pas par le cache
# HuggingFace habituel — il **clone un dépôt git** et tire les poids en LFS.
# Sans cette extension, le clone réussit, le `lfs pull` échoue, et le message
# rendu est « Failed to pull latest changes, please try again », qui ne dit pas
# de quoi il s'agit.
RUN apt-get update && apt-get install -y --no-install-recommends git-lfs \
    && git lfs install --skip-repo \
    && rm -rf /var/lib/apt/lists/*

# La version que l'amont épingle. Le respecter n'est pas de la superstition :
# un modèle de diffusion rend un audio différent d'une version de torch à
# l'autre, et `ADR-004` demande qu'une version encore `SERVING` reste
# relançable à l'identique.
ARG TORCH_VERSION=2.1.1
ARG TORCHVISION_VERSION=0.16.1
RUN pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cu121 \
      "torch==${TORCH_VERSION}" "torchaudio==${TORCH_VERSION}" \
      "torchvision==${TORCHVISION_VERSION}"

# Les dépendances **d'inférence**, épinglées comme l'amont les épingle.
RUN pip install --no-cache-dir \
      "librosa==0.10.1" "numpy==1.26.2" "omegaconf==2.3.0" "scipy==1.11.4" \
      "soundfile==0.12.1" "resampy==0.4.2" "tqdm==4.66.1" "rich==13.7.0" \
      "celluloid==0.2.0" "matplotlib==3.8.1" "pandas==2.1.3" "ptflops==0.7.1.2" \
      "tabulate==0.8.10"

# `deepspeed` est requis **à l'import**, pas seulement à l'entraînement.
# Première intuition : l'écarter, puisque rien de l'inférence ne s'en sert.
# Faux, et le garde-fou plus bas l'a montré tout de suite —
# `enhancer/inference.py` importe `enhancer/train.py`, qui importe
# `DeepSpeedConfig` en tête de fichier. Le chemin d'inférence ne l'appelle
# jamais, mais Python le charge quand même. Il est donc installé, et il l'est
# pour une raison d'import, pas de calcul.
#
# Ses noyaux CUDA, eux, sont compilés à la demande et ne le seront jamais ici.
RUN pip install --no-cache-dir "deepspeed==0.12.4"

# `--no-deps` pour écarter **une** dépendance :
#
#   gradio  est une interface web. Ce conteneur n'en sert aucune : l'agent
#           parle HTTP à `server.py`, et rien d'autre ne l'atteint.
#
# Le reste est installé au-dessus, aux versions de l'amont. La vérification
# n'est pas cette liste mais l'import du module d'inférence, fait à la
# construction : s'il manque quoi que ce soit, l'image ne se construit pas.
ARG RESEMBLE_VERSION=0.0.1
RUN pip install --no-cache-dir --no-deps "resemble-enhance==${RESEMBLE_VERSION}"

# Le garde-fou du `--no-deps` ci-dessus. Un import qui échoue ici coûte une
# construction ; le même import qui échoue au premier travail d'un utilisateur
# coûte une machine qui a rejoint la ferme en promettant ce qu'elle ne sait
# pas faire.
RUN python -c "from resemble_enhance.enhancer.inference import denoise, enhance; print('inference importable')"

# --- Les poids, cuits ici et pas ailleurs ------------------------------------
# Les deux autres moteurs appellent leur bibliotheque pour telecharger ; celui-ci
# ne peut pas, parce que `run_dir()` vit dans `server.py` et qu'appeler
# `server.py` ici mettrait les poids DANS la couche du serveur — donc les
# ferait retelecharger a chaque correction, exactement ce qu'on cherche a
# eviter. Les quatre lignes sont donc reproduites, et le `test -f` final est ce
# qui garantit qu'elles n'ont pas divergé : c'est le fichier meme que
# `run_dir()` cherche pour decider qu'il n'a rien a faire.
#
# `GIT_LFS_SKIP_SMUDGE` : le clone ne tire que les pointeurs, le `lfs pull`
# amene les octets. Sans le drapeau, git tente les deux d'un coup et un echec
# ne dit pas lequel a echoue.
ARG RESEMBLE_REPO_URL=https://huggingface.co/ResembleAI/resemble-enhance
RUN GIT_LFS_SKIP_SMUDGE=1 git clone "${RESEMBLE_REPO_URL}" /weights/model_repo \
 && git -C /weights/model_repo lfs pull \
 && test -f /weights/model_repo/enhancer_stage2/hparams.yaml \
 && echo "poids resemble-enhance en place"

COPY server.py /opt/abo/server.py

EXPOSE 18100
CMD ["uvicorn", "server:app", "--app-dir", "/opt/abo", "--host", "0.0.0.0", "--port", "18100"]
