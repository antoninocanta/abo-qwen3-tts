# Worker Qwen3-TTS

Conteneur ABO exposant un contrat interne stable autour du moteur C
[`gabriele-mastrapasqua/qwen3-tts`](https://github.com/gabriele-mastrapasqua/qwen3-tts).

Un seul worker porte les trois usages : **Base** clone une voix, **CustomVoice**
la joue avec style et émotion, **VoiceDesign** en crée une depuis une
description. Côté ABO ce sont trois `model_versions` distinctes — une voix
épingle celle qui l'a créée (`ADR-004`) — mais un seul déploiement.

## Pourquoi ce wrapper

Le serveur HTTP du moteur ne sert **qu'une voix clonée par processus**, fixée au
démarrage par `--load-voice`, et n'expose pas l'enrôlement. ABO a besoin de
plusieurs voix dans un même chapitre. Ce wrapper appelle donc le binaire
directement.

## Contrat

| Route | Entrée | Sortie |
|---|---|---|
| `POST /enroll` | `reference` (WAV), `voice_name`, `language`, `reference_text` | le profil `.qvoice` (~25 Mo) + en-tête `X-Voice-Sha256` |
| `POST /synthesize` | `text`, `language`, `instruct`, `emotion`, et soit `voice` (fichier), soit `voice_sha256` (si déjà en cache), soit `preset_voice` | WAV |
| `GET /health` | — | état |

Le profil de voix appartient au backend ABO : il est rendu à l'appelant et
conservé ici seulement comme cache, pour éviter de renvoyer 25 Mo à chaque
segment d'un chapitre. Un cache absent répond `409 VOICE_NOT_CACHED` — le
backend renvoie alors le fichier. Rien d'irremplaçable ne vit sur la machine de
calcul (`ADR-001`).

## Mise en service

1. Créer un dépôt **public** `antoninocanta/abo-qwen3-tts` et y pousser ce
   dossier à la racine.
2. Dans *Settings → Secrets → Actions*, ajouter `DOCKERHUB_USERNAME` et
   `DOCKERHUB_TOKEN`. Le token se crée sur Docker Hub, en écriture seule sur le
   dépôt. Il ne transite jamais ailleurs.
3. Le push déclenche le build ; l'image part sur
   `antoninocanta/abo-qwen3-tts:v1`.
4. Côté Vast : template pointant sur cette image, avec
   `PYWORKER_REPO=https://github.com/antoninocanta/abo-qwen3-tts`, puis worker
   group sur l'endpoint existant.

## À vérifier au premier build

Deux points que la documentation amont ne fixe pas noir sur blanc et qui
peuvent demander un ajustement :

- ~~le nom exact des poids passés à `download_model.sh`~~ — confirmé : `--model base-large` et `--model large` ;
- l'existence du drapeau `--ref-text` à l'enrôlement. S'il n'existe pas, la
  transcription reste conservée côté ABO comme provenance durable, mais n'est
  pas transmise au moteur.

Le build échouera bruyamment sur ces points plutôt que de produire une image
silencieusement inutilisable.

## Un seul endpoint

Les trois usages Qwen — cloner, jouer, concevoir — partagent le **même moteur
C** et vivent dans **une seule image**, donc un seul endpoint Vast. Les séparer
aurait imposé un second endpoint, facturé 5 $ de solde minimum, pour faire
tourner exactement le même binaire.

Côté ABO ce sont trois `model_versions` distinctes (`ADR-004`) : une voix
épingle celle qui l'a créée, et retirer une version du catalogue ne casse rien.
Une identité de modèle n'a jamais eu à correspondre à un conteneur.

VoiceDesign ne rend **pas** de profil durable : le moteur produit un WAV, il n'a
pas de `--save-voice` dans ce mode. Le parcours est celui que `specs/16`
prévoyait pour une route sans identifiant persistant :

```text
description écrite  →  POST /design   → extrait WAV
extrait validé      →  POST /enroll   → profil .qvoice durable
lecture             →  POST /synthesize
```

Chatterbox et ClearerVoice sont des stacks PyTorch : ils iront dans une seconde
image commune, sur un second endpoint.

## Référencer l'image par digest

Un tag mobile comme `:v1` **n'est pas retéléchargé** par une machine qui l'a
déjà en cache : elle sert l'ancienne image, et le worker exécute du code
périmé sans que rien ne le signale. C'est arrivé, et ça coûte une location de
GPU pour rien.

Le build pousse donc aussi un tag immuable par commit
(`abo-qwen3-tts:<sha>`), et le template Vast référence l'image **par digest** :

```text
antoninocanta/abo-qwen3-tts@sha256:<digest>
```

Après chaque build, relever le digest et le reporter dans le template.

## `worker.py` est un chemin de transition

Ce fichier est un **proxy PyWorker, propre au serverless Vast.ai**. Il existe
parce que c'est le seul chemin qui fonctionne aujourd'hui, et il porte tout ce
que ce protocole impose : certificat signé au démarrage, variables
d'environnement de l'hébergeur, configuration de benchmark.

L'agent ABO le remplacera. Le moteur, lui, ne change pas : il expose déjà un
contrat HTTP local qui ne connaît ni Vast, ni ABO. C'est bien la seule partie
qui devait survivre.
