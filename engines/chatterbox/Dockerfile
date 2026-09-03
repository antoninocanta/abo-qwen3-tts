# Moteur ABO — Chatterbox VC, transfert de jeu (`PERFORMANCE_TRANSFER`).
#
# Construit sur la base commune : torch y est deja, et cette image ne stocke
# donc que ce que Chatterbox ajoute.
#
#   docker build -t abo-chatterbox:dev engines/chatterbox
#
# Les poids SONT dans l'image, ecrits sous le `COPY server.py` (`ADR-009` § 9).
# Une machine qui tire cette image est prete a servir, et corriger le serveur ne
# coute que la derniere couche — `docker pull` ne recupere que ce qui manque.
#
# Ne PAS monter de volume sur `/weights` : il masquerait les poids cuits, ce qui
# donnerait le pire des deux mondes — une image lourde ET un telechargement au
# premier travail d'un utilisateur.
# Le socle est reference par un ARG pour que la meme recette serve ici et
# ailleurs : en local `abo-engine-base:cuda` suffit, en CI on epingle le tag
# du commit courant pour que moteur et socle ne puissent pas diverger.
ARG BASE_IMAGE=antoninocanta/abo-engine-base:cuda
FROM ${BASE_IMAGE}

# Chatterbox tire ses propres dependances audio. `--no-deps` serait tentant
# pour proteger la version de torch, mais il manquerait alors s3tokenizer,
# librosa et le reste : on laisse pip resoudre, et on verifie apres coup que
# torch n'a pas ete remplace (voir le README).
ARG CHATTERBOX_VERSION=0.1.7
RUN pip install --no-cache-dir "chatterbox-tts==${CHATTERBOX_VERSION}"

# --- Les poids, cuits ici et pas ailleurs ------------------------------------
# On appelle **le chemin de code du serveur**, pas un telechargement ecrit a la
# main : c'est ce qui garantit que les octets atterrissent exactement la ou
# `model()` ira les chercher. Un `snapshot_download` d'un depot devine
# rendrait une image lourde ET un telechargement au premier travail.
#
# `device="cpu"` : la machine qui construit n'a pas de carte. Le placement du
# modele ne change pas les fichiers telecharges.
#
# Cette couche est AVANT le `COPY server.py`, et cet ordre est tout l'interet :
# corriger le serveur n'invalide que la couche du dessus.
RUN python -c "from chatterbox.vc import ChatterboxVC; ChatterboxVC.from_pretrained(device='cpu'); print('poids chatterbox en place')"

COPY server.py /opt/abo/server.py

EXPOSE 18100
CMD ["uvicorn", "server:app", "--app-dir", "/opt/abo", "--host", "0.0.0.0", "--port", "18100"]
