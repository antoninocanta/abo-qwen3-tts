"""Serveur de modele ABO pour Qwen3-TTS.

Le serveur HTTP fourni par le moteur ne sert qu'une voix clonee par processus,
fixee au demarrage, et n'expose pas l'enrolement. ABO a besoin de plusieurs
voix dans un meme chapitre, et de creer des voix a la demande : ce wrapper
appelle donc le binaire directement et expose un contrat stable.

Tout passe en JSON, audio compris, encode en base64 : c'est ce que le proxy
PyWorker relaie, et un corps multipart n'y survivrait pas.

Ce qui entre et sort d'ici n'est jamais conserve durablement : le profil de
voix appartient au backend ABO, pas a la machine de calcul (ADR-001).
"""
import asyncio
import base64
import binascii
import hashlib
import logging
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

ENGINE = Path(os.getenv("QWEN_ENGINE", "/opt/qwen3-tts/qwen_tts"))
MODEL_DIR = Path(os.getenv("QWEN_MODEL_DIR", "/opt/qwen3-tts"))
# Trois jeux de poids, un binaire : Base clone, CustomVoice joue, VoiceDesign
# invente.
MODEL_BASE = os.getenv("QWEN_MODEL_BASE", "qwen3-tts-1.7b-base")
MODEL_CUSTOM = os.getenv("QWEN_MODEL_CUSTOM", "qwen3-tts-1.7b")
MODEL_DESIGN = os.getenv("QWEN_MODEL_DESIGN", "qwen3-tts-voice-design")
VOICE_CACHE = Path(os.getenv("QWEN_VOICE_CACHE", "/var/cache/abo/voices"))
TIMEOUT = float(os.getenv("QWEN_TIMEOUT_SECONDS", "600"))

VOICE_CACHE.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(os.getenv("QWEN_LOG_FILE", "/var/log/abo-qwen.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("abo.qwen")

app = FastAPI(title="abo-qwen3-tts")


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1)
    language: str = "French"
    instruction: str = ""
    emotion: str = ""
    preset_voice: str = ""
    voice_sha256: str = ""
    voice_b64: str = ""


class EnrollRequest(BaseModel):
    reference_b64: str = Field(min_length=1)
    voice_name: str = Field(min_length=1)
    language: str = "French"
    reference_text: str = ""


class DesignRequest(BaseModel):
    description: str = Field(min_length=1)
    text: str = Field(min_length=1)
    language: str = "French"


def _decode(value: str, what: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail=f"{what} illisible.") from None


async def _run(args: list[str]) -> None:
    """Invoque le moteur. Une erreur remonte tronquee, jamais un chemin interne."""
    process = await asyncio.create_subprocess_exec(
        str(ENGINE),
        *args,
        cwd=str(MODEL_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=TIMEOUT)
    except TimeoutError:
        process.kill()
        raise HTTPException(status_code=504, detail="Synthese trop longue.") from None

    if process.returncode != 0:
        tail = (output or b"").decode("utf-8", "replace")[-400:]
        logger.error("engine failed rc=%s %s", process.returncode, tail)
        raise HTTPException(status_code=502, detail="Moteur en erreur.")


def _cached_voice(sha256: str) -> Path:
    return VOICE_CACHE / (sha256 + ".qvoice")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "engine": ENGINE.exists()}


@app.post("/enroll")
async def enroll(payload: EnrollRequest) -> dict:
    """WAV de reference -> profil .qvoice.

    Le profil est rendu au backend et n'est conserve ici que comme cache : la
    voix appartient a ABO, la machine n'en est que l'atelier.
    """
    workdir = Path(tempfile.mkdtemp(prefix="enroll-"))
    wav = workdir / "reference.wav"
    wav.write_bytes(_decode(payload.reference_b64, "Audio de reference"))

    profile = workdir / "voice.qvoice"
    args = [
        "-d", MODEL_BASE,
        "--ref-audio", str(wav),
        "-l", payload.language,
        "--voice-name", payload.voice_name,
        "--save-voice", str(profile),
    ]
    if payload.reference_text:
        # La transcription exacte du sample ameliore le conditionnement.
        args += ["--ref-text", payload.reference_text]

    await _run(args)
    if not profile.exists():
        raise HTTPException(status_code=502, detail="Profil de voix non produit.")

    data = profile.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    _cached_voice(digest).write_bytes(data)
    logger.info("enrolled voice sha=%s size=%s", digest[:12], len(data))

    return {
        "voice_b64": base64.b64encode(data).decode("ascii"),
        "sha256": digest,
        "size_bytes": len(data),
    }


@app.post("/synthesize")
async def synthesize(payload: SynthesizeRequest) -> dict:
    """Texte -> WAV, avec une voix clonee ou une voix native du moteur.

    Le profil peut etre omis s'il est deja en cache : renvoyer 25 Mo a chaque
    segment d'un chapitre serait absurde. Un cache absent est dit explicitement
    au lieu d'etre devine.
    """
    workdir = Path(tempfile.mkdtemp(prefix="tts-"))
    output = workdir / "out.wav"
    args = ["-d", MODEL_CUSTOM, "--text", payload.text, "-l", payload.language, "-o", str(output)]

    if payload.voice_b64:
        data = _decode(payload.voice_b64, "Profil de voix")
        digest = hashlib.sha256(data).hexdigest()
        if payload.voice_sha256 and digest != payload.voice_sha256:
            raise HTTPException(status_code=422, detail="Empreinte de voix incoherente.")
        profile = _cached_voice(digest)
        profile.write_bytes(data)
        args += ["--load-voice", str(profile), "--icl-only"]
    elif payload.voice_sha256:
        cached = _cached_voice(payload.voice_sha256)
        if not cached.exists():
            raise HTTPException(status_code=409, detail="VOICE_NOT_CACHED")
        args += ["--load-voice", str(cached), "--icl-only"]
    elif payload.preset_voice:
        args += ["-s", payload.preset_voice]

    if payload.instruction:
        args += ["--instruct", payload.instruction]
    if payload.emotion:
        args += ["--emotion", payload.emotion]

    await _run(args)
    if not output.exists():
        raise HTTPException(status_code=502, detail="Audio non produit.")

    audio = output.read_bytes()
    return {
        "audio_b64": base64.b64encode(audio).decode("ascii"),
        "format": "wav",
        "size_bytes": len(audio),
    }


@app.post("/design")
async def design(payload: DesignRequest) -> dict:
    """Description ecrite -> extrait audio d'une voix inventee.

    Ce mode ne produit pas de profil durable : le moteur rend un WAV, pas un
    .qvoice. La preview validee devient donc la reference de clonage, enrolee
    ensuite par /enroll. C'est le chemin prevu par specs/16 pour une route
    Voice Design sans identifiant persistant.
    """
    if not (MODEL_DIR / MODEL_DESIGN).exists():
        raise HTTPException(status_code=501, detail="VoiceDesign absent de cette image.")

    workdir = Path(tempfile.mkdtemp(prefix="design-"))
    output = workdir / "preview.wav"
    await _run(
        [
            "-d", MODEL_DESIGN,
            "-l", payload.language,
            "--instruct", payload.description,
            "--text", payload.text,
            "-o", str(output),
        ]
    )
    if not output.exists():
        raise HTTPException(status_code=502, detail="Extrait non produit.")

    audio = output.read_bytes()
    return {
        "audio_b64": base64.b64encode(audio).decode("ascii"),
        "format": "wav",
        "size_bytes": len(audio),
    }


@app.exception_handler(HTTPException)
async def _error(_, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.on_event("startup")
def announce() -> None:
    # Le PyWorker detecte la disponibilite en lisant cette ligne dans le log.
    logger.info("ABO_QWEN_READY id=%s", uuid.uuid4().hex[:8])
