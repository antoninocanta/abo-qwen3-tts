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

## Mesuré : la résidence marche, et elle ne gagne rien

Deux parcours identiques le 31/08, six segments chacun sur une voix clonée.

| | Chemin | Carte | Segments à chaud (médiane) |
|---|---|---|---|
| avant | rechargement par appel | RTX 5070 | **10,0 s** |
| après | `engine=resident` sur les 6 appels | RTX 5070 Ti | **14,5 s** |

Le champ `engine` confirme que le pool a bien servi : ce n'est pas un repli
silencieux. Et la carte du second essai est la **plus rapide** des deux.

**Donc le chargement des poids n'était pas le coût dominant d'un segment.** Les
11,5 s du 31/08 sont du calcul réel. Supprimer le rechargement ne les enlève
pas, et le pool seul ne rend pas un chapitre praticable.

D'où venait l'erreur : le HANDOFF lisait « le benchmark mesure 2,3 » comme un
temps par segment. `measured_perf` est un **débit**, en unités par seconde, et
le `workload_calculator` de `worker.py` compte les caractères. À 5,6 unités/s
mesurées sur la 5070, une phrase de 55 caractères vaut ~10 s — exactement ce
qu'on observe. La cible « ~2 s » n'a jamais existé.

**Ce que le pool sert quand même.** `--batch-size` et `--prefork` du moteur
n'existent **qu'en mode serveur** : sans processus résident, ils sont hors
d'atteinte. Le pool est donc la marche d'avant, pas le gain lui-même.

**Pistes réelles, non mesurées** : `--int8` / `--int4` sur le Talker, le
`--batch-size N` façon vLLM, `--prefork N` qui partage les poids en
copy-on-write, et le modèle 0.6B. C'est là qu'il faut chercher désormais.

**Toujours pas mesuré** : la VRAM réellement occupée par résident, qui décidera
si le plafond de 2 est le bon.

## Ce que l'agent ABO apporte, lui

L'indépendance vis-à-vis de l'hébergeur : connexion sortante, pull du travail,
plus de protocole Vast. C'est le sujet de `specs/17` côté backend, et il reste
le chemin critique — mais pour cette raison-là, pas pour la vitesse.
