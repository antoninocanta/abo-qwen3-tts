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

Le dépôt `antoninocanta/abo-qwen3-tts` porte désormais **tout `abo-worker`** ;
ce moteur y vit sous `engines/qwen3_tts/`, et c'est le contexte de build.

1. Secrets *Settings → Secrets → Actions* : `DOCKERHUB_USERNAME` et
   `DOCKERHUB_TOKEN`. Le token se crée sur Docker Hub, en écriture seule sur le
   dépôt. Il ne transite jamais ailleurs.
2. Un push sur `main` déclenche le workflow `engines` : les tests d'abord,
   l'image ensuite. Un test rouge ne produit pas d'image.
3. L'image part sur `antoninocanta/abo-qwen3-tts:v1`, `:latest` et `:<sha>`.
4. Côté Vast : template pointant sur cette image **par digest** (voir plus
   bas), avec `PYWORKER_REPO=https://github.com/antoninocanta/abo-qwen3-tts`,
   puis worker group sur l'endpoint existant.

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

## Mesures réelles — 31/08/2026, RTX 3090 louée

Parcours complet éprouvé de bout en bout : description → extrait → profil →
lecture clonée → direction de jeu.

| Étape | Résultat | Durée |
|---|---|---|
| `/design` | extrait 207 Ko | 13,4 s |
| `/enroll` | profil `.qvoice` **25,2 Mo** | 26,0 s |
| `/synthesize`, profil envoyé | audio 204 Ko | **38,3 s** |
| `/synthesize`, empreinte seule | audio 146 Ko | **11,5 s** |
| `/synthesize` + instruction + émotion | audio 127 Ko | 13,4 s |
| empreinte inconnue | `VOICE_NOT_CACHED` | 1,9 s |

Deux enseignements, tous deux structurants.

**Le cache de voix n'est pas une optimisation, c'est la condition.** Renvoyer le
profil coûte 27 secondes de plus par appel — 38,3 s contre 11,5 s. Sur un
chapitre de trois cents segments, c'est plus de deux heures d'écart pour un
résultat identique. Le backend doit envoyer le profil une fois, puis ne
transmettre que son empreinte.

**Le moteur rechargeait ses poids à chaque appel.** Le benchmark de l'autoscaler
mesure 2,3 sur ce même matériel, mais un segment coûtait 11,5 s : le
rechargement dominait largement le calcul. Onze secondes par segment rendent un
chapitre impraticable.

La cause était ici, pas ailleurs : `server.py` lançait le binaire par
`create_subprocess_exec` à chaque requête. **Ce n'est pas l'agent ABO qui
corrige ça** — il aurait pu être écrit en entier sans que la seconde par segment
bouge. Voir « Résidence des poids » ci-dessous.

## Résidence des poids

Les synthèses passent par un **pool de processus `--serve` gardés vivants**. Le
chargement est payé une fois par voix, puis amorti sur tous les segments qui
suivent.

Ce que le mode serveur du moteur impose, et qui dessine tout le reste :

- une **voix clonée est fixée au démarrage** par `--load-voice` ; le corps de
  requête ne connaît que les voix natives (`speaker`). Un résident porte donc
  une voix, et servir une autre voix veut dire un autre processus ;
- il n'expose **ni enrôlement ni voice design**. `/enroll` et `/design` restent
  des invocations uniques — c'est sans conséquence, elles n'arrivent qu'une fois
  par voix.

D'où les propriétés du pool :

| | |
|---|---|
| Clé | `(modèle, voix)` |
| Plafond | `QWEN_MAX_RESIDENT`, **2** par défaut |
| Éviction | le plus anciennement utilisé, jamais un résident occupé |
| Concurrence | sérialisée par résident — une carte ne fait pas deux synthèses plus vite qu'une |
| Repli | si le pool ne peut pas servir, la synthèse a lieu quand même en rechargeant les poids |

Le plafond est une contrainte matérielle, pas un réglage de confort : chaque
résident garde un jeu de poids complet en VRAM, et le dépasser fait tomber la
carte en OOM au milieu d'un chapitre. `QWEN_POOL=0` rend le comportement
d'avant, une invocation par synthèse.

**Conséquence pour le backend, quand il attribuera les jobs** : grouper les
segments par voix. Alterner deux voix sur un pool de deux tient ; en alterner
cinq ferait payer un chargement à chaque segment, c'est-à-dire pire qu'avant.

## Le backend : la carte ne sert que si on la demande

**Sans `--backend cuda`, le moteur reste entièrement sur le CPU.** C'est écrit
dans son `main.c` :

> *« passing no `--backend` (or `--backend cpu`) leaves the engine 100% on the
> CPU path. »*

`make cuda` compile le support, il ne l'active pas. Trois mesures ont donc
tourné sur le CPU d'hôtes loués, carte payée et inactive, avant qu'on s'en
aperçoive.

La recette complète est en trois morceaux, tous nécessaires :

| | |
|---|---|
| `--backend cuda` | passé à chaque invocation par `server.py` |
| `QWEN_CUDA_FUSED_TALKER=1` | Talker + Code Predictor fusionnés, résidents |
| `QWEN_CUDA_CONVDEC=1` | décodeur de parole sur GPU — **sans lui il reste sur le CPU hôte**, et l'amont mesure l'écart entre RTF 0,94 et 0,39 |

`QWEN_BACKEND=` vide rend le chemin CPU sans reconstruire l'image : c'est ce
qui permet à une machine sans carte de servir quand même.

### Comment on l'a vu

Trois cartes très différentes rendaient la même chose, ce qui n'arrive pas
quand le GPU travaille :

| Carte | Chemin | Segments à chaud (médiane) |
|---|---|---|
| RTX 3090 | rechargement par appel | 11,5 s |
| RTX 5070 | rechargement par appel | 10,0 s |
| RTX 5070 Ti | `engine=resident` sur les 6 appels | 14,5 s |

La plus rapide des trois était la plus lente. Et la résidence n'a rien gagné —
normal : sur CPU, « recharger les poids » veut dire les relire depuis le page
cache du noyau, ce qui est presque gratuit. Sur GPU au contraire cela signifie
re-téléverser ~3,9 Go vers la VRAM, et là le pool devient déterminant.

**On avait donc mesuré la résidence dans le seul régime où elle ne pouvait pas
compter.**

### Ce qu'on attend maintenant

Convention amont : `RTF = temps de calcul ÷ durée d'audio`, **< 1 = plus rapide
que le temps réel**. Un segment ABO fait ~4,2 s d'audio.

| Contexte | RTF 1.7B | Un segment |
|---|---|---|
| **Nous, sur CPU** | 3,4 | 14,5 s |
| CPU EPYC 9555P, int8, `-j4` | 1,16 | ~5 s |
| CUDA, carte 4060-class, int8 | 0,55 | ~2,3 s |
| CUDA, `--quant-mixed` | 0,44 | ~1,8 s |
| CUDA, estimation 3090/4080-class | 0,13–0,17 | ~0,7 s |

Notre 3,4 était cohérent avec du CPU de cloud médiocre, et **8 à 25× au-dessus**
de ce que la carte devait rendre.

Prudence sur l'extrapolation : l'amont mesure **0,50 sur A100**, là où la table
par bande passante prédisait ~0,1 — passé un seuil, le décodage mono-flux
devient limité par le lancement de kernels. Viser **~2 s de calcul**, pas 0,7.

### Ce qui reste vrai du pool

`--batch-size` et `--prefork` n'existent **qu'en mode serveur** : sans processus
résident ils sont hors d'atteinte. Et sur GPU, la résidence évite un
téléversement de VRAM par appel. Le pool était la bonne marche, posée avant que
l'escalier soit allumé.

**Plafond et VRAM.** Chaque résident garde un jeu de poids complet : ~3,9 Go en
bf16 pour le 1.7B. Sur 8 Go, `QWEN_MAX_RESIDENT=1` ; sur 16 Go et plus, 2 tient.
La quantification (`--int8`, `--quant-mixed`) fait tomber l'empreinte et desserre
ce plafond. La VRAM réellement occupée n'est toujours pas mesurée.

## Faire tourner le moteur seul, sans Vast

`ABO_ENGINE_ONLY=1` lance **uniquement** le serveur de modèle : pas de
certificat Vast à faire signer, pas de proxy PyWorker. C'est le mode de la ferme
ABO — l'agent est un conteneur séparé qui joint le moteur par le réseau interne
(`deploy/docker-compose.yml`) — et c'est aussi celui d'un essai local.

```bash
docker run --rm --gpus all -e ABO_ENGINE_ONLY=1 -p 18100:18100 \
  antoninocanta/abo-qwen3-tts:v1
```

Sur Windows : Docker Desktop avec l'intégration WSL2 et un pilote NVIDIA récent
suffisent — `--gpus all` y fonctionne, sans installer Linux.

En mode moteur seul, uvicorn est le processus principal, donc **`docker logs`
montre le moteur**. C'est exactement ce qui manquait pour diagnostiquer un
worker serverless, où le log vit dans un fichier inatteignable.

Une machine sans carte : ajouter `-e QWEN_BACKEND=` et retirer `--gpus all`.

## Ce que l'agent ABO apporte, lui

L'indépendance vis-à-vis de l'hébergeur : connexion sortante, pull du travail,
plus de protocole Vast. C'est le sujet de `specs/17` côté backend, et il reste
le chemin critique — mais pour cette raison-là, pas pour la vitesse.
