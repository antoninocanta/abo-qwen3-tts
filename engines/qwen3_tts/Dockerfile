# Worker ABO — Qwen3-TTS 1.7B, moteur C avec backend CUDA.
#
# Les poids sont cuits dans l'image : avec cold_workers a 0, un reveil qui
# retelecharge plusieurs gigaoctets couterait plus cher que le stockage.

FROM nvidia/cuda:12.8.1-devel-ubuntu22.04 AS build

ARG QWEN_REF=main
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      git build-essential libopenblas-dev ca-certificates curl python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone --depth 1 --branch "${QWEN_REF}" \
      https://github.com/gabriele-mastrapasqua/qwen3-tts.git qwen3-tts

WORKDIR /opt/qwen3-tts
# ARCH_FLAGS : le Makefile utilise `-march=native` par defaut. Le runner qui
# compile n'est PAS la machine qui executera : un binaire taille pour l'AVX-512
# du runner planterait en SIGILL sur un hote Vast plus ancien. On fige donc une
# base commune a tout x86_64 moderne.
#
# NVCC_ARCH : le defaut amont commence a sm_80 et son repli PTX est en
# compute_120, qui ne peut pas redescendre. Un V100 (sm_70) etait donc exclu
# alors qu'il coute dix fois moins cher qu'une 5090. On ajoute sm_70 et 75, et
# le repli PTX passe en compute_70 pour couvrir tout ce qui est plus recent.
RUN make cuda ARCH_FLAGS="-mavx2 -mfma" NVCC_ARCH="-gencode arch=compute_70,code=sm_70 -gencode arch=compute_75,code=sm_75 -gencode arch=compute_80,code=sm_80 -gencode arch=compute_86,code=sm_86 -gencode arch=compute_89,code=sm_89 -gencode arch=compute_120,code=sm_120 -gencode arch=compute_70,code=compute_70"

# Trois jeux de poids, un seul moteur : Base clone une voix, CustomVoice la
# joue avec style et emotion, VoiceDesign en invente une depuis une
# description. Les separer aurait impose un second endpoint Vast, facture
# 5 $ de solde minimum, pour un moteur strictement identique.
RUN bash download_model.sh --model base-large \
 && bash download_model.sh --model large \
 && bash download_model.sh --model voice-design \
 && bash download_assets.sh


FROM nvidia/cuda:12.8.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    QWEN_ENGINE=/opt/qwen3-tts/qwen_tts \
    QWEN_MODEL_DIR=/opt/qwen3-tts \
    QWEN_MODEL_BASE=qwen3-tts-1.7b-base \
    QWEN_MODEL_CUSTOM=qwen3-tts-1.7b \
    QWEN_MODEL_DESIGN=qwen3-tts-voice-design \
    QWEN_VOICE_CACHE=/var/cache/abo/voices \
    QWEN_LOG_FILE=/var/log/abo-qwen.log \
    QWEN_BACKEND=cuda \
    QWEN_CUDA_FUSED_TALKER=1 \
    WORKER_PORT=3000 \
    REPORT_ADDR=https://run.vast.ai \
    USE_SSL=true

RUN apt-get update && apt-get install -y --no-install-recommends \
      libopenblas0 python3 python3-pip ca-certificates openssl curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/qwen3-tts /opt/qwen3-tts

COPY requirements.txt /opt/abo/requirements.txt
RUN pip3 install --no-cache-dir -r /opt/abo/requirements.txt

COPY server.py worker.py entrypoint.sh /opt/abo/
RUN chmod +x /opt/abo/entrypoint.sh
RUN mkdir -p /var/cache/abo/voices && touch /var/log/abo-qwen.log

# QWEN_CUDA_CONVDEC EST VOLONTAIREMENT ABSENT. Le decodeur de parole sur GPU
# rend un souffle uniforme au lieu de la voix — mesure sur RTX 2060 Super, et le
# meme symptome sur RTX 5070 Ti. C est la seule des deux variables qui casse ;
# le Talker fusionne, lui, divise le temps par trois et rend de la parole.
# L amont le documente comme un gain, mais sa colonne CUDA est marquee
# « GPU-compile-pending » : ecrite en miroir des kernels Metal, jamais executee
# sur une carte NVIDIA. Ne pas l ajouter sans reecouter la sortie.
#
# 3000 : le port du proxy PyWorker, seul point d entree. Le serveur de modele
# reste sur 127.0.0.1:18100 et n est jamais joignable de l exterieur.
EXPOSE 3000
# L image est autonome : elle porte le serveur de modele ET le proxy PyWorker.
# PYWORKER_REPO n'est lu que par les images de base Vast, dont celle-ci ne
# descend pas : rien ne clonerait ni ne lancerait worker.py.
CMD ["/opt/abo/entrypoint.sh"]
