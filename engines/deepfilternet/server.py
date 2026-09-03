"""Moteur de nettoyage : de l'audio entre, le meme audio en sort sans le bruit.

Ce serveur ne sait rien d'ABO. Il ne connait ni job, ni compte, ni Abollard :
il recoit un WAV, appelle DeepFilterNet, et rend un WAV. C'est l'agent qui
parle au backend, et c'est ce partage qui permet de remplacer un moteur sans
toucher au reste (`specs/17`).

Deux choses que ce fichier fait et qu'il serait tentant de sauter.

**Il normalise l'entree avant de la donner au modele.** DeepFilterNet a ete
entraine en 48 kHz mono. Une prise de telephone en 16 kHz stereo produirait un
resultat plausible et faux — le genre de defaut qui ne se voit pas dans un
format de fichier et s'entend seulement a l'ecoute. La conversion passe par
`audioop`, qui est dans la bibliotheque standard : y ajouter numpy ou librosa
tirerait des dizaines de mega-octets pour un reechantillonnage.

**Il verifie que ce qui sort est de l'audio.** Le binaire peut echouer en
rendant un fichier vide ou du silence sans jamais sortir en erreur. Quatre
campagnes de mesure ont deja rendu de belles durees sur du souffle ; une
mesure porte une verification de contenu, sinon elle ne mesure rien.
"""
import audioop
import base64
import binascii
import io
import logging
import os
import shutil
import struct
import subprocess
import tempfile
import time
import wave
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

DF_BINARY = os.getenv("DF_BINARY", "/usr/local/bin/deep-filter")
TARGET_RATE = int(os.getenv("DF_SAMPLE_RATE", "48000"))
# Un nettoyage n'est pas une synthese : il tourne en temps quasi reel sur
# processeur. Une minute d'audio qui met plus de cinq minutes signale une
# machine saturee, pas un travail long.
TIMEOUT_SECONDS = float(os.getenv("DF_TIMEOUT_SECONDS", "300"))
# `JOB_MAX_AUDIO_SECONDS` vaut 120 cote backend. La borne locale est plus large
# a dessein : le moteur n'est pas l'endroit ou l'on decide de la politique de
# service, il se protege seulement d'une entree absurde.
MAX_INPUT_BYTES = int(os.getenv("DF_MAX_INPUT_BYTES", str(200 * 1024 * 1024)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("abo.deepfilternet")

app = FastAPI(title="ABO — DeepFilterNet")


class EnhanceRequest(BaseModel):
    audio_b64: str
    # Ce que la **route** dit a ce moteur. La machine ne le choisit pas : deux
    # versions de modele peuvent porter deux reglages du meme binaire.
    config: dict = {}


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


def normalise(raw: bytes) -> tuple[bytes, dict]:
    """Ramene une entree quelconque en WAV 16 bits, mono, 48 kHz.

    Rend aussi ce qui a ete change : sans cette trace, un resultat decevant ne
    se distingue pas d'une entree qui n'etait pas celle qu'on croyait.
    """
    with wave.open(io.BytesIO(raw), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
        frames = source.readframes(source.getnframes())

    if not frames:
        raise ValueError("Cette entree ne porte aucun echantillon.")

    changed = {"channels": channels, "sampleWidth": width, "sampleRate": rate}

    if width != 2:
        frames = audioop.lin2lin(frames, width, 2)
        width = 2
    if channels > 1:
        frames = audioop.tomono(frames, width, 0.5, 0.5)
        channels = 1
    if rate != TARGET_RATE:
        frames, _ = audioop.ratecv(frames, width, channels, rate, TARGET_RATE, None)
        rate = TARGET_RATE

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(TARGET_RATE)
        target.writeframes(frames)
    return buffer.getvalue(), changed


def inspect(raw: bytes) -> dict:
    """Ce que porte reellement un WAV : un pic, du silence, une duree.

    C'est la verification de contenu. Un fichier de la bonne taille et de la
    bonne duree peut ne contenir que du souffle, et seule une mesure du signal
    le dit.
    """
    with wave.open(io.BytesIO(raw), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
        rate = handle.getframerate()
        count = handle.getnframes()

    samples = struct.unpack(f"<{len(frames) // 2}h", frames) if frames else ()
    if not samples:
        return {"peak": 0, "silenceRatio": 1.0, "durationSeconds": 0.0}

    peak = max(abs(value) for value in samples)
    # Le seuil est volontairement bas : on cherche « il ne se passe rien »,
    # pas « c'est trop doux ».
    quiet = sum(1 for value in samples if abs(value) < 64)
    return {
        "peak": peak,
        "silenceRatio": round(quiet / len(samples), 4),
        "durationSeconds": round(count / rate, 3) if rate else 0.0,
    }


def run_deep_filter(wav_bytes: bytes, config: dict) -> bytes:
    """Appelle le binaire sur un fichier, et rend ce qu'il a ecrit.

    Le binaire travaille en fichiers et non en flux. Le repertoire temporaire
    est detruit dans tous les cas : une prise appartient a l'utilisateur, et
    elle n'a aucune raison de survivre au traitement sur la machine.
    """
    with tempfile.TemporaryDirectory(prefix="abo-df-") as workspace:
        root = Path(workspace)
        source = root / "in.wav"
        outdir = root / "out"
        outdir.mkdir()
        source.write_bytes(wav_bytes)

        # `--compensate-delay` n'est pas une option de confort. Sans elle, la
        # sortie est decalee du temps de la STFT et du lookahead du modele : le
        # fichier a la bonne duree et la parole n'y tombe plus au meme endroit.
        # Une prise doit rester alignee sur le texte qui l'a produite, et sur
        # les autres prises du chapitre.
        command = [DF_BINARY, "--compensate-delay", "--output-dir", str(outdir)]
        # `atten_lim_db` borne l'attenuation appliquee au bruit. A 100 dB — le
        # defaut amont — le modele efface le souffle **et** la respiration, ce
        # qui rend une voix propre et morte. C'est un reglage de gout, donc il
        # vit sur la route et pas dans l'image.
        attenuation = config.get("atten_lim_db")
        if attenuation is not None:
            command += ["--atten-lim-db", str(attenuation)]
        if config.get("post_filter"):
            command.append("--pf")
        command.append(str(source))

        started = time.monotonic()
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS
        )
        elapsed = time.monotonic() - started

        if result.returncode != 0:
            raise RuntimeError(
                f"deep-filter a echoue ({result.returncode}) : "
                f"{(result.stderr or result.stdout or '').strip()[:300]}"
            )

        produced = sorted(outdir.glob("*.wav"))
        if not produced:
            # Le binaire nomme sa sortie lui-meme et le suffixe a change entre
            # versions : on prend ce qu'il a ecrit plutot que de parier sur un
            # nom. S'il n'a rien ecrit, c'est un echec, pas un silence.
            raise RuntimeError("deep-filter n'a produit aucun fichier.")

        logger.info("nettoyage en %.2f s -> %s", elapsed, produced[0].name)
        return produced[0].read_bytes()


@app.get("/health")
def health() -> dict:
    """Le moteur est-il utilisable ? La reponse porte le binaire, pas un `ok`.

    Un agent qui rejoint la ferme declare ce qu'il sait faire. Repondre sain
    sans verifier que le binaire existe ferait entrer dans la ferme une machine
    qui echouera au premier travail d'un utilisateur.
    """
    present = shutil.which(DF_BINARY) is not None or Path(DF_BINARY).is_file()
    return {
        "status": "ok" if present else "degraded",
        "engine": present,
        "enginePath": "deepfilternet3",
        "sampleRate": TARGET_RATE,
    }


@app.post("/enhance")
def enhance(request: EnhanceRequest):
    try:
        raw = base64.b64decode(request.audio_b64, validate=True)
    except (binascii.Error, ValueError):
        return _fail(422, "audio_b64 illisible")
    if not raw:
        return _fail(422, "audio vide")
    if len(raw) > MAX_INPUT_BYTES:
        return _fail(413, "entree trop volumineuse")

    try:
        prepared, source_format = normalise(raw)
    except (wave.Error, EOFError, ValueError) as failure:
        return _fail(422, f"entree illisible : {failure}")

    try:
        cleaned = run_deep_filter(prepared, request.config or {})
    except subprocess.TimeoutExpired:
        return _fail(504, "le nettoyage n'a pas abouti dans le temps imparti")
    except (RuntimeError, OSError) as failure:
        return _fail(502, str(failure))

    try:
        measured = inspect(cleaned)
    except (wave.Error, EOFError, struct.error):
        return _fail(502, "le moteur a rendu un fichier qui n'est pas un WAV")

    if measured["peak"] == 0:
        # Un silence complet n'est jamais un nettoyage reussi. Le refuser ici
        # evite de facturer une prise muette et de la decouvrir a l'ecoute.
        return _fail(502, "le moteur a rendu du silence")

    logger.info(
        "entree %s -> pic %s, silences %.1f%%, %.2f s",
        source_format,
        measured["peak"],
        measured["silenceRatio"] * 100,
        measured["durationSeconds"],
    )
    return {
        "audio_b64": base64.b64encode(cleaned).decode("ascii"),
        "format": "wav",
        "size_bytes": len(cleaned),
        "engine": "deepfilternet3",
        # Remonte a l'agent, qui les transmet au backend en telemetrie. Ce sont
        # des mesures de la machine : elles servent au cout et au placement,
        # jamais au prix (`ABOB-102`).
        "peak": measured["peak"],
        "silence_ratio": measured["silenceRatio"],
        "duration_seconds": measured["durationSeconds"],
    }
