#!/usr/bin/env sh
# Le moteur et l'agent dans le meme conteneur, pour une machine louee.
#
# Sur un PC qu'on possede, ces deux-la vivent dans deux conteneurs et se
# parlent par le reseau du compose. Sur une instance louee il n'y a qu'une
# image, donc un seul processus racine : celui-ci demarre le moteur en fond et
# passe la main a l'agent.
set -eu

PORT="${ENGINE_PORT:-18100}"

# `127.0.0.1` et non `0.0.0.0` : l'agent est dans le meme conteneur, et rien
# d'autre n'a a joindre le moteur. Sur une machine qui appartient a quelqu'un
# d'autre, ce detail est le seul qui separe « un moteur local » de « un service
# ouvert sur un hote inconnu ».
echo "moteur : uvicorn sur 127.0.0.1:${PORT}"
uvicorn server:app --app-dir /opt/abo --host 127.0.0.1 --port "$PORT" &
ENGINE_PID=$!

# Si le moteur meurt, l'agent n'a plus rien a servir. On tombe avec lui plutot
# que de laisser une machine enrolee promettre une capacite morte — l'heure est
# facturee de toute facon, autant qu'elle s'arrete.
trap 'kill -TERM "$ENGINE_PID" 2>/dev/null || true' EXIT

# Rien a synchroniser ici, et c'est voulu : l'agent interroge `/health` et
# attend `engine: true` avant de s'enroler (`ABOB-128`). Attendre est son
# metier, pas celui de ce script — un `sleep` arbitraire serait trop court le
# jour ou le disque est lent, et trop long tous les autres jours.
echo "agent : demarrage, il attendra son moteur"
exec python /opt/abo/agent.py
