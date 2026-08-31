#!/usr/bin/env bash
# Prepare une machine Ubuntu a devenir un worker ABO.
#
#   sudo ./deploy/install-ubuntu.sh
#
# Installe Docker et le NVIDIA Container Toolkit, verifie que le GPU est vu
# depuis un conteneur, et s'arrete la. L'enrolement du worker est une etape
# separee : elle demande un secret, et un script d'installation n'est pas
# l'endroit ou on colle un secret.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "A lancer en root." >&2
  exit 1
fi

echo "== Docker =="
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
else
  echo "deja installe"
fi

echo "== NVIDIA Container Toolkit =="
if ! command -v nvidia-ctk >/dev/null 2>&1; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update
  apt-get install -y nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
else
  echo "deja installe"
fi

echo "== Verification GPU dans un conteneur =="
# Sans cette verification, la premiere panne se decouvrirait au premier job,
# sur une machine deja enrolee et supposee saine.
if docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu22.04 nvidia-smi >/dev/null 2>&1; then
  docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu22.04 \
    nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader
  echo ""
  echo "Machine prete. Enrolement : voir docs/enrolement.md"
else
  echo "Le GPU n'est pas visible depuis un conteneur." >&2
  exit 1
fi
