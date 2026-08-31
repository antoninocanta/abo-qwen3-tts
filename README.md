# abo-worker

Unité de calcul d'ABO. Un PC équipé d'un GPU devient un worker : on l'installe,
il se déclare, il reçoit du travail.

Statut : **l'agent existe et tire du travail** ; le moteur Qwen3-TTS est
éprouvé sur GPU.

## L'idée

Le calcul n'est pas un fournisseur, c'est une **ferme de machines**. Un PC
personnel, un serveur loué, une instance Vast.ai à la demande : toutes font
tourner le même agent et parlent le même protocole.

```text
ABO Control Plane  →  Compute Pool  →  workers
                                       ├─ PC perso (RTX 3090)
                                       ├─ serveur distant
                                       └─ Vast.ai, pour le débordement
```

Acheter demain un PC d'occasion doit vouloir dire : brancher, installer,
rejoindre la ferme. Vast.ai devient le **turbo à la demande**, pas le cœur de
l'architecture.

## Deux briques

| | Rôle |
|---|---|
| **agent** | se déclare au backend, envoie son pouls, tire du travail, rend le résultat |
| **moteurs** | conteneurs qui savent faire une chose : synthèse, clonage, nettoyage |

L'agent ne sait rien des modèles ; un moteur ne sait rien d'ABO. Entre les
deux, un contrat HTTP local sur `127.0.0.1`.

## Connexion sortante uniquement

Un worker **n'ouvre aucun port**. Il compose vers le backend en HTTPS et tire
son travail. C'est ce qui rend un PC derrière une box domestique utilisable
sans toucher au routeur, et c'est ce qui évite d'exposer une machine
personnelle sur Internet.

L'administration passe par un VPN de type Tailscale — pour s'y connecter à la
main, pas pour faire circuler les jobs.

## Répertoires

```text
abo_worker/
  agent/      l'agent ABO : s'enrôle, tire du travail, rend le résultat
  engines/    un répertoire par moteur
    qwen3_tts/    synthèse, clonage, voice design
  deploy/     installation d'une machine et lancement des conteneurs
  docs/       ce qui est propre au worker
  .github/    construction et publication des images
```

## Le protocole fait autorité côté backend

Le contrat entre un worker et ABO — enregistrement, pouls, attribution d'un
job, remise du résultat — est spécifié dans le dépôt du backend :
`abo_backend/specs/17-workers-et-ferme-de-calcul.md`. Ce dépôt-ci l'implémente,
il ne le définit pas.

## État

- `engines/qwen3_tts` : image construite et publiée, contrat `/enroll`,
  `/synthesize`, `/design` opérationnel en local.
- **Le GPU est enfin demandé.** Sans `--backend cuda`, le moteur reste
  entièrement sur le CPU — trois mesures ont tourné sur des cartes louées et
  inactives avant qu'on le voie. Corrigé, **pas encore mesuré**.
- **Résidence des poids** : les synthèses passent par un pool de processus
  `--serve` gardés vivants, un par voix, au lieu d'une invocation par segment.
  Neuf tests sans GPU ; sur carte, elle évite un téléversement de ~3,9 Go par
  appel.
- **Moteur seul** : `ABO_ENGINE_ONLY=1` lance le serveur de modèle sans
  certificat Vast ni proxy. C'est le mode de la ferme — l'agent est un conteneur
  séparé — et celui d'un essai local, Windows et Docker Desktop compris.
- `engines/qwen3_tts/worker.py` : proxy PyWorker, **spécifique au serverless
  Vast.ai**. L'agent ABO le remplace désormais ; il ne sert plus que si l'on
  revient au serverless de Vast.
- `agent/` : **écrit et éprouvé de bout en bout**. Il s'enrôle, envoie son
  pouls, tire un job, appelle le moteur sur `127.0.0.1` et rend le résultat.
  Aucun port ouvert. `agent/tests/fake_engine.py` rend un WAV valide pour
  éprouver la chaîne sans carte.

```bash
cd engines/qwen3_tts && chmod +x tests/fake_qwen.py && python -m pytest tests -q
```
