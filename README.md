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
  engines/    un répertoire par moteur, plus le contrat qu'ils suivent tous
    CONTRACT.md      ce qu'un moteur doit servir, et ce qu'il doit vérifier
    base/            socle commun aux moteurs qui ont besoin de torch
    qwen3_tts/       synthèse, clonage, voice design
    deepfilternet/   nettoyage de bruit, sur processeur
    clearervoice/    rehaussement 48 kHz, sur GPU
    chatterbox/      transfert de jeu (« Follow My Lead »)
  deploy/     installation d'une machine et lancement des conteneurs
  docs/       ce qui est propre au worker
  .github/    construction et publication des images
```

## Ce qu'une machine porte, elle le choisit

Les moteurs sont derrière des **profils** compose. Une machine sans carte ne
lance que ce qui tourne sur processeur ; une machine qui en a une choisit ce
qu'elle veut servir. Déclarer dans `ABO_ENGINES` un moteur qu'on ne fait pas
tourner fait entrer dans la ferme une machine qui échouera au premier travail
d'un utilisateur.

```bash
docker compose --profile enhance-cpu up -d                 # nettoyage, sans GPU
docker compose --profile tts --profile transfer up -d      # synthèse + jeu
```

## Les poids ne sont pas dans les images — sauf pour Qwen

| moteur | poids | pourquoi |
|---|---|---|
| `qwen3_tts` | **dans l'image** | sur une instance louée réveillée à froid, retélécharger plusieurs gigaoctets coûte plus cher que le stockage |
| les trois autres | **montés** depuis `ABO_ENGINE_DATA` | sur un PC qu'on possède, cette raison tombe — et une image qui porte ses poids se retélécharge en entier à chaque correction du serveur |

```bash
ABO_ENGINE_DATA=/mnt/disque-de-travail docker compose --profile transfer up -d
```

Les poids se chargent une fois au démarrage : un disque lent ne coûte que ce
démarrage, c'est le calcul qui décide du reste.

## Ce que chaque moteur fait, mesuré

Sur RTX 2060 Super, 03/09/2026, même prise de 5,6 s volontairement bruitée.
**Chaque mesure porte une vérification de contenu** — une belle durée sur du
souffle a déjà été rendue quatre fois sur ce projet.

| moteur | opération | mesuré | où |
|---|---|---|---|
| `deepfilternet` | `AUDIO_ENHANCE` | 31 dB de bruit retiré, **94 %** de parole, RTF **0,25** | processeur |
| `clearervoice` | `AUDIO_ENHANCE` | 85 dB de bruit retiré, **97 %** de parole | GPU |
| `chatterbox` | `PERFORMANCE_TRANSFER` | enveloppe **+0,96** avec la performance contre +0,80 avec le timbre, RTF **0,24** | GPU |

Les deux premiers servent la **même** opération à deux qualités : le client
demande `AUDIO_ENHANCE`, jamais un nom de moteur. Le troisième chiffre de
Chatterbox est celui qui compte — il dit que la sortie a gardé le **jeu** de la
performance et pas celui de la voix de référence, ce qui est toute la promesse.

`clearervoice` efface en revanche **toute** la pièce entre les mots (plancher de
bruit à 0). C'est plus propre et ce n'est pas forcément mieux : à écouter avant
d'en faire un défaut.

### Les trois tiennent ensemble sur une carte de 8 Go

Éprouvé en alternance — transfert, nettoyage GPU, nettoyage CPU, transfert,
nettoyage GPU — chaque travail rendu en 2 à 4 secondes, sortie vérifiée comme
parole à chaque fois : **2,8 Go de VRAM sur 8**, aucune tentative perdue.

C'est l'inverse de Qwen, et la différence mérite d'être connue avant de peupler
une machine : Qwen garde un jeu de poids complet **par voix résidente** (~3,9 Go
en bf16 pour le 1.7B), d'où `QWEN_MAX_RESIDENT=1` sur 8 Go. Ces trois-là gardent
un modèle chacun et n'en changent pas selon la voix. Une même machine peut donc
porter le nettoyage et le transfert sans arbitrage ; y ajouter Qwen demande de
compter.

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
