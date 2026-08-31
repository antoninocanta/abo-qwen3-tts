"""L'agent ABO : la machine se declare, tire du travail, rend le resultat.

Tout part d'ici. L'agent **compose vers** le backend en HTTPS et n'ecoute sur
aucun port : c'est ce qui permet a un PC derriere une box domestique de servir
sans toucher au routeur, et a une instance louee pour dix minutes de travailler
sans qu'on lui monte un reseau prive d'abord.

    enrolement  ->  pouls  ->  bail  ->  moteur local  ->  resultat

L'agent ne sait rien des modeles et rien des prix. Il porte des moteurs, il
declare lesquels, et le backend lui attribue ce qu'il reconnait. Ce qui transite
ne reste pas : un texte, un profil de voix, un extrait passent et repartent.

Configuration, par l'environnement :

    ABO_BACKEND_URL      racine de l'API ABO
    ABO_WORKER_KEY       identifiant de la machine, donne a sa creation
    ABO_WORKER_SECRET    secret d'enrolement, affiche une seule fois
    ABO_ENGINES          moteurs portes, separes par des virgules :
                         `engineKey|modelKey|versionNumber|url`
    ABO_WORKER_GPU       carte declaree, quand l'agent ne peut pas la voir
                         lui-meme (il tourne dans son propre conteneur)
"""
import logging
import os
import platform
import shutil
import subprocess
import sys
import time

import httpx

AGENT_VERSION = "0.1.0"

BACKEND_URL = os.getenv("ABO_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
WORKER_KEY = os.getenv("ABO_WORKER_KEY", "")
WORKER_SECRET = os.getenv("ABO_WORKER_SECRET", "")
ENGINES_SPEC = os.getenv("ABO_ENGINES", "")
DECLARED_GPU = os.getenv("ABO_WORKER_GPU", "")

# Un intervalle court fait vivre le mode Creation : l'utilisateur attend devant
# son ecran, et deux secondes de sondage s'ajoutent a chaque segment. Un bail
# long et un pouls lent suffisent au reste.
POLL_SECONDS = float(os.getenv("ABO_AGENT_POLL_SECONDS", "2"))
HEARTBEAT_SECONDS = float(os.getenv("ABO_AGENT_HEARTBEAT_SECONDS", "30"))
# Le moteur peut mettre des dizaines de secondes a charger ses poids a froid,
# et un chapitre entier bien davantage.
ENGINE_TIMEOUT = float(os.getenv("ABO_AGENT_ENGINE_TIMEOUT", "900"))
BACKEND_TIMEOUT = float(os.getenv("ABO_AGENT_BACKEND_TIMEOUT", "120"))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
)
logger = logging.getLogger("abo.agent")


class Engine:
    """Un moteur local, et la version de modele ABO qu'il sert."""

    def __init__(self, engine_key: str, model_key: str, version_number: int, url: str) -> None:
        self.engine_key = engine_key
        self.model_key = model_key
        self.version_number = version_number
        self.url = url.rstrip("/")

    def declaration(self) -> dict:
        return {
            "engineKey": self.engine_key,
            "modelKey": self.model_key,
            "versionNumber": self.version_number,
        }


def parse_engines(spec: str) -> list[Engine]:
    """`engineKey|modelKey|versionNumber|url`, separes par des virgules.

    Un moteur mal decrit arrete l'agent au demarrage. Se declarer a moitie
    reviendrait a rejoindre la ferme en promettant une capacite qu'on ne sert
    pas, et l'erreur ne se verrait qu'au premier job d'un utilisateur.
    """
    engines: list[Engine] = []
    for entry in (part.strip() for part in spec.split(",")):
        if not entry:
            continue
        fields = [field.strip() for field in entry.split("|")]
        if len(fields) != 4 or not all(fields):
            raise SystemExit(
                f"ABO_ENGINES mal forme : « {entry} ». "
                "Attendu : engineKey|modelKey|versionNumber|url"
            )
        engine_key, model_key, version, url = fields
        if not version.isdigit():
            raise SystemExit(f"Numero de version invalide dans « {entry} ».")
        engines.append(Engine(engine_key, model_key, int(version), url))

    if not engines:
        raise SystemExit("ABO_ENGINES est vide : cette machine n'a rien a servir.")
    return engines


def hardware() -> dict:
    """Ce que la machine dit d'elle-meme.

    Declaratif et non verifie : le backend s'en sert pour placer un travail,
    jamais pour accorder un droit. L'agent vit dans son propre conteneur et ne
    voit pas forcement la carte du moteur — d'ou `ABO_WORKER_GPU`.
    """
    info: dict = {
        "platform": platform.platform(),
        "cpuCount": os.cpu_count(),
        "machine": platform.machine(),
    }
    if DECLARED_GPU:
        info["gpu"] = DECLARED_GPU

    if shutil.which("nvidia-smi"):
        try:
            output = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            ).stdout.strip()
        except (subprocess.SubprocessError, OSError) as failure:
            logger.warning("nvidia-smi injoignable : %s", failure)
        else:
            if output:
                name, _, memory = output.splitlines()[0].partition(",")
                info["gpu"] = name.strip()
                info["vram"] = memory.strip()
    return info


class Backend:
    """Le seul interlocuteur distant de l'agent, toujours en sortant."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._base = f"{BACKEND_URL}/v1/workers/{WORKER_KEY}"
        self._headers = {"X-Worker-Secret": WORKER_SECRET}

    def _post(self, path: str, payload: dict) -> httpx.Response:
        return self._client.post(
            self._base + path, json=payload, headers=self._headers, timeout=BACKEND_TIMEOUT
        )

    def enrol(self, engines: list[Engine]) -> None:
        response = self._post(
            "/enrol",
            {
                "agentVersion": AGENT_VERSION,
                "hardware": hardware(),
                "engines": [engine.declaration() for engine in engines],
            },
        )
        if response.status_code == 403:
            raise SystemExit("Cette machine a ete revoquee. Arret.")
        if response.status_code == 422:
            # Un moteur que le backend ne connait pas n'est pas une capacite
            # ignoree en silence : c'est une machine mal configuree.
            raise SystemExit(f"Declaration refusee : {response.text}")
        response.raise_for_status()
        logger.info("enrole : %s", response.json())

    def heartbeat(self, running: int) -> dict:
        response = self._post("/heartbeat", {"load": {"running": running}})
        if response.status_code == 403:
            raise SystemExit("Cette machine a ete revoquee. Arret.")
        response.raise_for_status()
        return response.json()

    def lease(self) -> dict | None:
        response = self._post("/lease", {})
        if response.status_code == 204:
            return None
        if response.status_code == 409:
            # La machine n'est plus attribuable : elle devra se redeclarer.
            logger.warning("bail refuse : %s", response.text)
            return None
        response.raise_for_status()
        return response.json()

    def voice(self, sha256: str) -> str:
        """Va chercher un profil que cette machine n'a pas encore."""
        response = self._client.get(
            f"{self._base}/voices/{sha256}",
            headers=self._headers,
            timeout=BACKEND_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()["voiceB64"]

    def result(self, job_id: str, attempt: int, payload: dict) -> None:
        response = self._post(f"/jobs/{job_id}/result", {**payload, "attempt": attempt})
        response.raise_for_status()
        logger.info("rendu job=%s -> %s", job_id, response.json().get("status"))

    def failure(self, job_id: str, attempt: int, error_class: str, detail: str) -> None:
        response = self._post(
            f"/jobs/{job_id}/failure",
            {"attempt": attempt, "errorClass": error_class, "detail": detail[:500]},
        )
        response.raise_for_status()
        logger.info("echec signale job=%s -> %s", job_id, response.json().get("status"))


class EngineError(RuntimeError):
    """Le moteur local a refuse ou n'a rien rendu."""


def _post_engine(client: httpx.Client, url: str, body: dict) -> httpx.Response:
    return client.post(url, json=body, timeout=ENGINE_TIMEOUT)


def synthesize(
    client: httpx.Client, engine: Engine, job_input: dict, backend: "Backend"
) -> dict:
    """Texte -> WAV. Le profil de voix appartient au backend, pas a la machine.

    Seule l'**empreinte** arrive avec le travail : renvoyer 25 Mo a chaque
    segment d'un chapitre serait absurde. Si la machine n'a pas encore ce
    profil, le moteur le dit (`409 VOICE_NOT_CACHED`) au lieu de le deviner, et
    on va le chercher une fois. Les segments suivants passent par le cache.
    """
    body = {
        "text": job_input.get("text", ""),
        "language": job_input.get("language", "French"),
        "instruction": job_input.get("instruction", "") or "",
        "emotion": job_input.get("emotion", "") or "",
        "preset_voice": job_input.get("presetVoice", "") or "",
        "voice_sha256": job_input.get("voiceSha256", "") or "",
    }

    response = _post_engine(client, f"{engine.url}/synthesize", body)
    if response.status_code == 409 and body["voice_sha256"]:
        # Le chemin lent, et il doit le rester : une fois par machine et par
        # voix. Le profil ne devient pas durable ici, il alimente un cache.
        logger.info("profil absent du cache, recuperation : %s", body["voice_sha256"][:12])
        body["voice_b64"] = backend.voice(body["voice_sha256"])
        response = _post_engine(client, f"{engine.url}/synthesize", body)

    if response.status_code != 200:
        raise EngineError(f"{response.status_code} {response.text[:300]}")

    payload = response.json()
    audio = payload.get("audio_b64")
    if not audio:
        raise EngineError("Le moteur n'a rendu aucun audio.")
    return {
        "audioB64": audio,
        "format": payload.get("format", "wav"),
        # Ce que la machine a mesure. Sert au cout interne et au placement ;
        # le prix, lui, est recalcule par le backend a partir de l'entree.
        "metrics": {
            "sizeBytes": payload.get("size_bytes", 0),
            "enginePath": payload.get("engine", "unknown"),
        },
    }


def enrol_voice(
    client: httpx.Client, engine: Engine, job_input: dict, backend: "Backend"
) -> dict:
    """Echantillon + transcription -> profil de voix.

    Le profil repart vers le backend, a qui la voix appartient. Ce qui reste
    ici n'est qu'un cache, jetable : le perdre ne coute qu'un renvoi.
    """
    reference = job_input.get("referenceB64")
    if not reference:
        raise EngineError("Aucun echantillon de reference dans ce travail.")

    response = _post_engine(
        client,
        f"{engine.url}/enroll",
        {
            "reference_b64": reference,
            "voice_name": job_input.get("voiceName", "voix"),
            "language": job_input.get("language", "French"),
            "reference_text": job_input.get("referenceText", "") or "",
        },
    )
    if response.status_code != 200:
        raise EngineError(f"{response.status_code} {response.text[:300]}")

    payload = response.json()
    profile = payload.get("voice_b64")
    if not profile:
        raise EngineError("Le moteur n'a rendu aucun profil de voix.")
    return {
        "artifactB64": profile,
        # Le backend recalcule l'empreinte : celle-ci ne sert qu'a detecter une
        # corruption de transport.
        "artifactSha256": payload.get("sha256", ""),
        "metrics": {"sizeBytes": payload.get("size_bytes", 0)},
    }


HANDLERS = {"TTS": synthesize, "VOICE_CLONE": enrol_voice}


def execute(
    client: httpx.Client, engines: dict[str, Engine], assignment: dict, backend: "Backend"
) -> dict:
    engine = engines.get(assignment["engineKey"])
    if engine is None:
        # Le backend a attribue un travail pour un moteur que cette machine ne
        # porte pas : sa declaration et sa realite ont diverge.
        raise EngineError(f"Moteur non porte : {assignment['engineKey']}")

    handler = HANDLERS.get(assignment["operation"])
    if handler is None:
        raise EngineError(f"Operation non servie par cet agent : {assignment['operation']}")

    started = time.monotonic()
    payload = handler(client, engine, assignment.get("input") or {}, backend)
    payload["metrics"]["computeMs"] = int((time.monotonic() - started) * 1000)
    return payload


def run() -> None:
    if not WORKER_KEY or not WORKER_SECRET:
        raise SystemExit("ABO_WORKER_KEY et ABO_WORKER_SECRET sont requis.")

    engines = parse_engines(ENGINES_SPEC)
    by_key = {engine.engine_key: engine for engine in engines}
    logger.info(
        "agent %s -> %s, moteurs : %s",
        AGENT_VERSION,
        BACKEND_URL,
        ", ".join(f"{e.engine_key}@{e.model_key}v{e.version_number}" for e in engines),
    )

    with httpx.Client() as client:
        backend = Backend(client)
        backend.enrol(engines)

        last_heartbeat = 0.0
        while True:
            try:
                now = time.monotonic()
                if now - last_heartbeat >= HEARTBEAT_SECONDS:
                    pulse = backend.heartbeat(running=0)
                    last_heartbeat = now
                    if pulse.get("mustEnrol"):
                        # Le backend l'avait declaree perdue : ses capacites
                        # datent d'avant sa disparition, elle se redeclare.
                        logger.info("redeclaration demandee")
                        backend.enrol(engines)

                assignment = backend.lease()
                if assignment is None:
                    time.sleep(POLL_SECONDS)
                    continue

                job_id = assignment["jobId"]
                # Le bail vaut pour cette tentative-la : le backend refuse un
                # resultat rendu sur une tentative qu'il a declaree perdue.
                attempt = assignment["attempt"]
                logger.info(
                    "job=%s operation=%s tentative=%s",
                    job_id,
                    assignment["operation"],
                    attempt,
                )
                try:
                    payload = execute(client, by_key, assignment, backend)
                except (EngineError, httpx.HTTPError) as failure:
                    # Un travail qu'on ne sait pas faire se rend tout de suite :
                    # le backend le retentera ailleurs sans attendre le bail.
                    logger.warning("job=%s echec : %s", job_id, failure)
                    backend.failure(job_id, attempt, type(failure).__name__, str(failure))
                else:
                    backend.result(job_id, attempt, payload)

            except SystemExit:
                raise
            except httpx.HTTPError as failure:
                # Le backend est injoignable. On patiente : une coupure reseau
                # ne doit pas sortir une machine de la ferme.
                logger.warning("backend injoignable : %s", failure)
                time.sleep(POLL_SECONDS * 5)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        logger.info("arret demande")
