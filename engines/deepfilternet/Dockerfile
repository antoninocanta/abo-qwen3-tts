# Moteur ABO — DeepFilterNet 3, suppression de bruit sur processeur.
#
# Aucun torch, aucun poids a telecharger au demarrage : l'amont publie un
# binaire **statique** qui porte son modele. Le paquet Python equivalent
# tirerait torch et ses dependances CUDA, soit deux ordres de grandeur pour le
# meme resultat. C'est ce qui permet a ce moteur de tenir sur une machine sans
# carte, et de se construire quand il ne reste que quelques gigaoctets.
#
# Le binaire est en musl, donc statique : il tourne tel quel sur une base
# glibc, sans bibliotheque a installer.
FROM python:3.12-slim

ARG DF_VERSION=0.5.6
# Epingle par empreinte : un binaire retelecharge n'est pas le meme objet, et
# une version de modele encore SERVING doit rester relancable a l'identique
# (`ADR-004`).
ARG DF_SHA256=

ENV PYTHONUNBUFFERED=1 \
    DF_BINARY=/usr/local/bin/deep-filter \
    ENGINE_PORT=18100 \
    # 48 kHz est la frequence a laquelle le modele a ete entraine. Une entree
    # d'une autre frequence est reechantillonnee par le serveur, jamais donnee
    # telle quelle : le moteur rendrait un resultat plausible et faux.
    DF_SAMPLE_RATE=48000

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL -o /usr/local/bin/deep-filter \
        "https://github.com/Rikorose/DeepFilterNet/releases/download/v${DF_VERSION}/deep-filter-${DF_VERSION}-x86_64-unknown-linux-musl" \
    && if [ -n "$DF_SHA256" ]; then \
         echo "${DF_SHA256}  /usr/local/bin/deep-filter" | sha256sum -c -; \
       fi \
    && chmod +x /usr/local/bin/deep-filter \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/abo
COPY requirements.txt /opt/abo/requirements.txt
RUN pip install --no-cache-dir -r /opt/abo/requirements.txt

COPY server.py /opt/abo/server.py

# Le moteur n'est joignable que par l'agent, sur le reseau du compose. Il
# n'ouvre rien vers l'exterieur et ne connait pas ABO : il prend de l'audio et
# en rend.
EXPOSE 18100
CMD ["uvicorn", "server:app", "--app-dir", "/opt/abo", "--host", "0.0.0.0", "--port", "18100"]
