"""Serveur de modele ABO pour Qwen3-TTS.

Le serveur HTTP fourni par le moteur ne sert qu'une voix clonee par processus,
fixee au demarrage par --load-voice, et n'expose pas l'enrolement. ABO a besoin
de plusieurs voix dans un meme chapitre, et de creer des voix a la demande : ce
wrapper pilote donc lui-meme les processus du moteur et expose un contrat
stable.

Residence des poids
-------------------
Lancer le binaire a chaque segment coutait 11,5 s la ou le calcul en vaut ~2 :
le chargement des poids dominait tout. Les syntheses passent donc par un *pool*
de processus `--serve` gardes vivants, un par (modele, voix) ; le chargement est
paye une fois, puis amorti sur tous les segments qui suivent.

L'enrolement et le voice design restent des invocations uniques : le mode
serveur du moteur ne les expose pas, et ils n'arrivent qu'une fois par voix.

Tout passe en JSON, audio compris, encode en base64 : c'est ce que le proxy
PyWorker relaie, et un corps multipart n'y survivrait pas.

Ce qui entre et sort d'ici n'est jamais conserve durablement : le profil de
voix appartient au backend ABO, pas a la machine de calcul (ADR-001).
"""
import asyncio
import base64
import binascii
import contextlib
import hashlib
import logging
import os
import socket
import tempfile
import time
import uuid
from pathlib import Path

import httpx
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

# Sans `--backend`, le moteur reste **entierement sur le CPU** — c'est ecrit
# noir sur blanc dans son `main.c`. Louer une carte n'y change rien : elle
# dormirait. Le support CUDA est compile dans l'image (`make cuda`), il ne
# manquait que de l'allumer.
#
# Vider la variable rend le chemin CPU, sans reconstruire l'image : c'est la
# sortie de secours si un hote refuse le backend.
BACKEND = os.getenv("QWEN_BACKEND", "cuda").strip()

# Chaque resident garde un jeu de poids complet en VRAM. Le plafond est donc une
# contrainte materielle, pas un reglage de confort : le depasser fait tomber la
# carte en OOM au milieu d'un chapitre. Deux tient sur 24 Go avec de la marge.
MAX_RESIDENT = int(os.getenv("QWEN_MAX_RESIDENT", "2"))
# Fenetre de ports du pool. Jamais exposee : tout vit sur la boucle locale.
PORT_BASE = int(os.getenv("QWEN_POOL_PORT_BASE", "18200"))
# Charger 1,7 Md de parametres sur une carte froide prend des dizaines de
# secondes ; ce delai borne l'attente avant de declarer le processus mort.
READY_TIMEOUT = float(os.getenv("QWEN_READY_TIMEOUT_SECONDS", "180"))
# Un pool desactive rend le chemin d'avant : une invocation par synthese. C'est
# la sortie de secours si le mode serveur se revele indisponible sur une image.
POOL_ENABLED = os.getenv("QWEN_POOL", "1") not in ("0", "false", "no")

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
    """Invoque le moteur une fois. Une erreur remonte tronquee, jamais un chemin.

    Chemin des operations rares — enrolement, voice design — et repli du pool.
    Chaque appel recharge les poids : ne pas l'utiliser pour un chapitre.
    """
    process = await asyncio.create_subprocess_exec(
        str(ENGINE),
        *_backend_args(),
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


def _backend_args() -> list[str]:
    return ["--backend", BACKEND] if BACKEND else []


def _cached_voice(sha256: str) -> Path:
    return VOICE_CACHE / (sha256 + ".qvoice")


# --- Pool de processus residents ---------------------------------------------


class _PoolUnavailable(RuntimeError):
    """Le pool n'a pas pu fournir de moteur — le travail n'a donc pas eu lieu.

    Distinct d'une erreur du moteur : ici rien n'a tourne, donc reessayer en
    invocation unique ne recalcule rien et ne coute rien deux fois. Un moteur
    vivant qui refuse, lui, remonte en 502 sans repli.
    """


class _Resident:
    """Un processus `qwen_tts --serve`, ses poids charges, sa voix figee.

    La voix clonee ne se change pas a chaud : c'est un argument de demarrage du
    moteur. Un resident est donc identifie par la voix qu'il porte, et servir
    une autre voix veut dire un autre resident.
    """

    def __init__(self, key: str, port: int, process: asyncio.subprocess.Process) -> None:
        self.key = key
        self.port = port
        self.process = process
        self.last_used = time.monotonic()
        # Une carte ne calcule pas deux syntheses plus vite qu'une seule : les
        # requetes d'un meme resident sont serialisees plutot que mises en
        # concurrence sur la VRAM.
        self.lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return self.process.returncode is None

    async def stop(self) -> None:
        if not self.alive:
            return
        self.process.terminate()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.process.wait(), timeout=20)
        if self.alive:
            self.process.kill()
            with contextlib.suppress(Exception):
                await self.process.wait()


_pool: dict[str, _Resident] = {}
_pool_lock = asyncio.Lock()


def _free_port() -> int:
    """Un port libre dans la fenetre du pool, demande a l'OS plutot que devine."""
    for offset in range(64):
        candidate = PORT_BASE + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
        return candidate
    raise _PoolUnavailable("aucun port libre pour le moteur")


async def _await_ready(resident: _Resident) -> None:
    """Attend que le moteur reponde, ou constate qu'il est mort en chargeant.

    Le port s'ouvre quand les poids sont en place : c'est donc la disponibilite
    reelle qu'on mesure, pas un delai suppose.
    """
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if not resident.alive:
            raise _PoolUnavailable("moteur arrete au chargement")
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", resident.port)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.5)
    raise _PoolUnavailable("moteur trop long a charger")


async def _evict_oldest_locked() -> None:
    """Libere la VRAM du resident le plus ancien. Appele sous `_pool_lock`."""
    idle = [r for r in _pool.values() if not r.lock.locked()]
    victim = min(idle or list(_pool.values()), key=lambda r: r.last_used)
    _pool.pop(victim.key, None)
    logger.info("engine evicted key=%s port=%s", victim.key, victim.port)
    await victim.stop()


async def _resident_for(model: str, voice: Path | None, preset: str) -> _Resident:
    """Le processus qui porte cette voix, demarre s'il n'existe pas encore.

    Le premier segment d'une voix paie le chargement ; les suivants ne paient
    plus que le calcul. C'est tout l'objet du pool.
    """
    key = f"{model}:{voice.name if voice else preset or 'default'}"
    async with _pool_lock:
        existing = _pool.get(key)
        if existing is not None:
            if existing.alive:
                existing.last_used = time.monotonic()
                return existing
            # Un moteur mort ne se repare pas : on l'oublie et on en relance un.
            _pool.pop(key, None)
            logger.warning("engine died key=%s rc=%s", key, existing.process.returncode)

        while len(_pool) >= MAX_RESIDENT:
            await _evict_oldest_locked()

        port = _free_port()
        args = [*_backend_args(), "-d", model, "--serve", str(port)]
        if voice is not None:
            args += ["--load-voice", str(voice), "--icl-only"]
        elif preset:
            args += ["-s", preset]

        process = await asyncio.create_subprocess_exec(
            str(ENGINE),
            *args,
            cwd=str(MODEL_DIR),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.STDOUT,
        )
        resident = _Resident(key, port, process)
        try:
            await _await_ready(resident)
        except _PoolUnavailable:
            await resident.stop()
            raise
        _pool[key] = resident
        logger.info("engine resident key=%s port=%s", key, port)
        return resident


async def _synthesize_resident(
    payload: SynthesizeRequest, model: str, voice: Path | None
) -> bytes:
    """Une synthese sur poids deja charges."""
    resident = await _resident_for(model, voice, payload.preset_voice)
    body: dict = {"text": payload.text, "language": payload.language}
    if payload.instruction:
        body["instruct"] = payload.instruction
    if payload.emotion:
        body["emotion"] = payload.emotion
    # La voix clonee est figee au demarrage ; `speaker` ne vaut que pour les voix
    # natives du moteur, et l'envoyer avec un clone charge le contredirait.
    if voice is None and payload.preset_voice:
        body["speaker"] = payload.preset_voice

    async with resident.lock:
        resident.last_used = time.monotonic()
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"http://127.0.0.1:{resident.port}/v1/tts", json=body
            )
    if response.status_code != 200:
        logger.error("engine http rc=%s", response.status_code)
        raise HTTPException(status_code=502, detail="Moteur en erreur.")
    return response.content


async def _synthesize_once(
    payload: SynthesizeRequest, model: str, voice: Path | None
) -> bytes:
    """Repli : une invocation, un chargement de poids, un WAV."""
    workdir = Path(tempfile.mkdtemp(prefix="tts-"))
    output = workdir / "out.wav"
    args = ["-d", model, "--text", payload.text, "-l", payload.language, "-o", str(output)]
    if voice is not None:
        args += ["--load-voice", str(voice), "--icl-only"]
    elif payload.preset_voice:
        args += ["-s", payload.preset_voice]
    if payload.instruction:
        args += ["--instruct", payload.instruction]
    if payload.emotion:
        args += ["--emotion", payload.emotion]

    await _run(args)
    if not output.exists():
        raise HTTPException(status_code=502, detail="Audio non produit.")
    return output.read_bytes()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "engine": ENGINE.exists(),
        "resident": [
            {"key": r.key, "busy": r.lock.locked()} for r in _pool.values() if r.alive
        ],
        "resident_max": MAX_RESIDENT,
    }


@app.post("/enroll")
async def enroll(payload: EnrollRequest) -> dict:
    """WAV de reference -> profil .qvoice.

    Le profil est rendu au backend et n'est conserve ici que comme cache : la
    voix appartient a ABO, la machine n'en est que l'atelier.

    Invocation unique : le mode serveur du moteur n'expose pas l'enrolement, et
    une voix ne s'enrole qu'une fois.
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

    Passe par un moteur resident quand c'est possible. Si le pool ne peut pas
    servir cette voix, la synthese a lieu quand meme, en rechargeant les poids :
    un chapitre lent vaut mieux qu'un chapitre echoue.
    """
    voice: Path | None = None
    if payload.voice_b64:
        data = _decode(payload.voice_b64, "Profil de voix")
        digest = hashlib.sha256(data).hexdigest()
        if payload.voice_sha256 and digest != payload.voice_sha256:
            raise HTTPException(status_code=422, detail="Empreinte de voix incoherente.")
        voice = _cached_voice(digest)
        voice.write_bytes(data)
    elif payload.voice_sha256:
        cached = _cached_voice(payload.voice_sha256)
        if not cached.exists():
            raise HTTPException(status_code=409, detail="VOICE_NOT_CACHED")
        voice = cached

    audio: bytes | None = None
    # `engine` dit lequel des deux chemins a produit l'audio. Sans lui, un pool
    # qui se replie silencieusement est indiscernable d'un pool qui marche mal :
    # les durees seules ne le disent pas, et le log du moteur n'est pas lisible
    # depuis un worker serverless.
    source = "reload"
    if POOL_ENABLED:
        try:
            audio = await _synthesize_resident(payload, MODEL_CUSTOM, voice)
            source = "resident"
        except _PoolUnavailable as failure:
            logger.warning("resident unavailable, falling back: %s", failure)
        except HTTPException:
            # Un moteur vivant qui refuse a deja fait le travail : le rejouer
            # paierait deux fois le meme calcul pour la meme erreur.
            raise
        except Exception as failure:  # noqa: BLE001 - resident perdu en cours
            logger.warning("resident lost, falling back: %s", failure)
    if audio is None:
        audio = await _synthesize_once(payload, MODEL_CUSTOM, voice)

    logger.info("synthesized engine=%s chars=%s", source, len(payload.text))
    return {
        "audio_b64": base64.b64encode(audio).decode("ascii"),
        "format": "wav",
        "size_bytes": len(audio),
        "engine": source,
    }


@app.post("/design")
async def design(payload: DesignRequest) -> dict:
    """Description ecrite -> extrait audio d'une voix inventee.

    Ce mode ne produit pas de profil durable : le moteur rend un WAV, pas un
    .qvoice. La preview validee devient donc la reference de clonage, enrolee
    ensuite par /enroll. C'est le chemin prevu par specs/16 pour une route
    Voice Design sans identifiant persistant.

    Invocation unique, comme l'enrolement : concevoir une voix est rare, et le
    mode serveur du moteur ne porte pas VoiceDesign.
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


@app.on_event("shutdown")
async def _drain() -> None:
    """Rendre la VRAM en partant : un moteur orphelin garderait la carte."""
    async with _pool_lock:
        residents = list(_pool.values())
        _pool.clear()
    for resident in residents:
        await resident.stop()
