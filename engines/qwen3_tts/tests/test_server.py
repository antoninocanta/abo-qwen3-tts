"""Ce que le pool doit garantir, eprouve sans carte graphique.

Le faux moteur trace chaque lancement. Compter ces lancements, c'est compter les
chargements de poids — la seule chose que ce chantier cherchait a supprimer.
"""
import base64
import hashlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).parent
WORK = Path(tempfile.mkdtemp(prefix="abo-qwen-tests-"))
LAUNCH_LOG = WORK / "launches.log"

os.environ.update(
    QWEN_ENGINE=str(HERE / "fake_qwen.py"),
    QWEN_MODEL_DIR=str(WORK),
    QWEN_VOICE_CACHE=str(WORK / "voices"),
    QWEN_LOG_FILE=str(WORK / "abo-qwen.log"),
    QWEN_POOL_PORT_BASE="18400",
    QWEN_READY_TIMEOUT_SECONDS="30",
    FAKE_QWEN_LOG=str(LAUNCH_LOG),
)

sys.path.insert(0, str(HERE.parent))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


def launches() -> list[str]:
    if not LAUNCH_LOG.exists():
        return []
    return [line for line in LAUNCH_LOG.read_text(encoding="utf-8").splitlines() if line]


def resident_launches() -> list[str]:
    return [line for line in launches() if "--serve" in line]


def voice(seed: bytes) -> str:
    return base64.b64encode(seed + b"\x00" * 64).decode("ascii")


def synthesize(client: TestClient, voice_b64: str, text: str = "Bonjour.") -> dict:
    response = client.post(
        "/synthesize", json={"text": text, "language": "French", "voice_b64": voice_b64}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture()
def client():
    LAUNCH_LOG.unlink(missing_ok=True)
    with TestClient(server.app) as running:
        yield running
    assert not server._pool, "un moteur resident a survecu a l'arret du serveur"


def test_une_voix_ne_charge_ses_poids_qu_une_fois(client):
    """Trois segments d'une meme voix : un seul chargement.

    C'est la promesse entiere du chantier. Si ce test tombe, chaque segment
    repaie le chargement et le chapitre redevient impraticable.
    """
    server.MAX_RESIDENT = 2
    victor = voice(b"victor")

    for _ in range(3):
        assert synthesize(client, victor)["format"] == "wav"

    assert len(resident_launches()) == 1


def test_deux_voix_tiennent_ensemble_sous_le_plafond(client):
    """Alterner deux voix ne relance rien tant que le pool les porte."""
    server.MAX_RESIDENT = 2
    victor, xylareth = voice(b"victor"), voice(b"xylareth")

    for speaker in (victor, xylareth, victor, xylareth):
        synthesize(client, speaker)

    assert len(resident_launches()) == 2


def test_le_plafond_evince_le_plus_ancien(client):
    """La VRAM est finie : au-dela du plafond, le plus ancien rend sa place.

    Et une voix evincee doit repayer son chargement quand elle revient — c'est
    ce qui impose au backend de grouper les segments par voix.
    """
    server.MAX_RESIDENT = 1
    victor, xylareth = voice(b"victor"), voice(b"xylareth")

    synthesize(client, victor)
    synthesize(client, xylareth)
    assert len(server._pool) == 1

    synthesize(client, victor)
    assert len(resident_launches()) == 3


def test_un_moteur_qui_ne_demarre_pas_ne_fait_pas_echouer_la_synthese(client, monkeypatch):
    """Le repli tient : poids recharges, mais un WAV rendu.

    Un chapitre lent vaut mieux qu'un chapitre echoue.
    """
    server.MAX_RESIDENT = 2
    monkeypatch.setenv("FAKE_QWEN_FAIL_SERVE", "1")

    payload = synthesize(client, voice(b"victor"))

    assert payload["format"] == "wav"
    # Le resident a bien ete tente, puis l'invocation unique a pris le relais.
    assert len(resident_launches()) == 1
    assert len(launches()) == 2
    assert not server._pool, "un moteur mort ne reste pas dans le pool"


def test_le_pool_desactive_rend_le_comportement_d_avant(client, monkeypatch):
    """`QWEN_POOL=0` : une invocation par synthese, comme avant le chantier."""
    monkeypatch.setattr(server, "POOL_ENABLED", False)

    synthesize(client, voice(b"victor"))
    synthesize(client, voice(b"victor"))

    assert not resident_launches()
    assert len(launches()) == 2


def test_une_empreinte_inconnue_est_dite_et_non_devinee(client):
    """`VOICE_NOT_CACHED` reste le contrat : le backend renvoie alors le profil."""
    response = client.post(
        "/synthesize",
        json={"text": "Bonjour.", "voice_sha256": hashlib.sha256(b"absente").hexdigest()},
    )

    assert response.status_code == 409
    assert response.json() == {"error": "VOICE_NOT_CACHED"}
    assert not launches()


def test_l_enrolement_reste_une_invocation_unique(client):
    """Le mode serveur du moteur n'expose pas l'enrolement : pas de resident."""
    server.MAX_RESIDENT = 2
    response = client.post(
        "/enroll",
        json={
            "reference_b64": base64.b64encode(b"RIFF" + b"\x00" * 40).decode("ascii"),
            "voice_name": "Victor",
            "language": "French",
        },
    )

    assert response.status_code == 200
    assert response.json()["size_bytes"] > 0
    assert not resident_launches()


