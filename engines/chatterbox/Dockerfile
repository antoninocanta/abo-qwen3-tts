# Moteur ABO — Chatterbox VC, transfert de jeu (`PERFORMANCE_TRANSFER`).
#
# Construit sur la base commune : torch y est deja, et cette image ne stocke
# donc que ce que Chatterbox ajoute.
#
#   docker build -t abo-chatterbox:dev engines/chatterbox
#
# Les poids ne sont PAS dans l'image. Ils se telechargent au premier appel dans
# `HF_HOME`, monte depuis le disque de travail :
#
#   docker run --gpus all -v D:/abo-engines/weights/chatterbox:/weights ...
#
# C'est ce qui permet de corriger le serveur sans retelecharger un gigaoctet,
# et de garder l'image assez petite pour tenir sur un disque systeme rempli.
FROM abo-engine-base:cuda

# Chatterbox tire ses propres dependances audio. `--no-deps` serait tentant
# pour proteger la version de torch, mais il manquerait alors s3tokenizer,
# librosa et le reste : on laisse pip resoudre, et on verifie apres coup que
# torch n'a pas ete remplace (voir le README).
ARG CHATTERBOX_VERSION=0.1.7
RUN pip install --no-cache-dir "chatterbox-tts==${CHATTERBOX_VERSION}"

COPY server.py /opt/abo/server.py

EXPOSE 18100
CMD ["uvicorn", "server:app", "--app-dir", "/opt/abo", "--host", "0.0.0.0", "--port", "18100"]
