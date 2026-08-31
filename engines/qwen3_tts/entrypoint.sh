#!/usr/bin/env bash
# Demarre les deux processus du worker.
#
# Le PyWorker n'est pas un serveur : c'est un proxy qui parle a un serveur de
# modele local et rapporte son etat a l'autoscaler. Les deux vivent donc dans le
# meme conteneur, et le conteneur meurt si l'un des deux tombe.
set -euo pipefail

LOG_FILE="${QWEN_LOG_FILE:-/var/log/abo-qwen.log}"
touch "$LOG_FILE"

# --- Certificat TLS -----------------------------------------------------------
# Le proxy sert en HTTPS et attend /etc/instance.{key,crt}. Sur les images de
# base Vast, leur script de demarrage les fabrique ; cette image n'en descend
# pas, elle doit donc le faire elle-meme : generer une CSR, la faire signer par
# Vast, ecrire la paire. Sans cela le proxy refuse de demarrer.
if [ "${USE_SSL:-true}" = "true" ]; then
  if [ -z "${CONTAINER_ID:-}" ]; then
    echo "CONTAINER_ID absent : impossible de faire signer le certificat" >> "$LOG_FILE"
    exit 1
  fi

  cat > /etc/openssl-san.cnf <<'CNF'
[req]
default_bits       = 2048
distinguished_name = req_distinguished_name
req_extensions     = v3_req

[req_distinguished_name]
countryName         = US
stateOrProvinceName = CA
organizationName    = Vast.ai Inc.
commonName          = vast.ai

[v3_req]
basicConstraints = CA:FALSE
keyUsage         = nonRepudiation, digitalSignature, keyEncipherment
subjectAltName   = @alt_names

[alt_names]
IP.1   = 0.0.0.0
CNF

  openssl req -newkey rsa:2048 -subj "/C=US/ST=CA/CN=pyworker.vast.ai/" \
    -nodes -sha256 \
    -keyout /etc/instance.key \
    -out /etc/instance.csr \
    -config /etc/openssl-san.cnf >> "$LOG_FILE" 2>&1

  signed=0
  delay=2
  for attempt in 1 2 3 4 5; do
    code=$(curl -sS -o /etc/instance.crt -w '%{http_code}' \
      --header 'Content-Type: application/octet-stream' \
      --data-binary @/etc/instance.csr \
      -X POST "https://console.vast.ai/api/v0/sign_cert/?instance_id=${CONTAINER_ID}" || echo 000)
    if [ "$code" -ge 200 ] && [ "$code" -lt 300 ]; then
      signed=1
      break
    fi
    echo "signature du certificat : tentative $attempt refusee (HTTP $code)" >> "$LOG_FILE"
    sleep "$delay"
    delay=$((delay * 2))
  done

  if [ "$signed" -ne 1 ]; then
    echo "certificat non signe apres 5 tentatives" >> "$LOG_FILE"
    exit 1
  fi
  echo "certificat TLS obtenu" >> "$LOG_FILE"
fi

# --- Serveur de modele --------------------------------------------------------
echo "starting model server" >> "$LOG_FILE"
uvicorn server:app \
  --app-dir /opt/abo \
  --host 127.0.0.1 \
  --port "${QWEN_SERVER_PORT:-18100}" \
  >> "$LOG_FILE" 2>&1 &
SERVER_PID=$!

# Si le serveur meurt, le proxy n'a plus rien a proxyfier : on tombe avec lui
# plutot que de laisser l'autoscaler croire a un worker sain.
trap 'kill -TERM "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 120); do
  if kill -0 "$SERVER_PID" 2>/dev/null; then
    if grep -q "ABO_QWEN_READY" "$LOG_FILE"; then
      break
    fi
  else
    echo "model server exited before becoming ready" >> "$LOG_FILE"
    exit 1
  fi
  sleep 1
done

# --- Proxy --------------------------------------------------------------------
echo "starting pyworker proxy" >> "$LOG_FILE"
exec python3 /opt/abo/worker.py
