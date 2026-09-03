# Le contrat d'un moteur

Un moteur ne sait rien d'ABO. Il ne connaît ni compte, ni job, ni Abollard :
il reçoit de l'audio ou du texte, il en rend. C'est l'agent qui parle au
backend, et c'est ce partage qui permet de remplacer un moteur sans toucher au
reste (`specs/17`).

Un moteur écoute sur `127.0.0.1` ou sur le réseau interne du compose. Il
**n'ouvre jamais rien vers Internet** et n'a besoin d'aucun secret ABO.

## Les règles qui valent pour tous

**L'audio circule en base64, jamais en fichier.** L'agent et le moteur peuvent
vivre dans deux conteneurs ; un chemin partagé serait un couplage de plus.

**Les clés sont en `snake_case`.** Le backend et l'agent parlent camelCase entre
eux ; à partir de l'agent, on descend en snake_case. La frontière est nette, et
c'est l'agent qui traduit.

**Un refus est un code HTTP, pas un `200` avec un champ d'erreur.** L'agent
distingue « cette machine ne sait pas faire » d'« elle a échoué » : le premier
renvoie le travail à la ferme tout de suite, le second compte une tentative.

| code | ce que l'agent en fait |
|---|---|
| `200` | résultat accepté |
| `409` | cas prévu et nommé (`VOICE_NOT_CACHED`) — l'agent corrige et rejoue |
| `422` | entrée invalide — échec, la reprise ailleurs ne changera rien |
| `501` | cette image ne porte pas cette capacité — le backend retentera ailleurs |
| `502`, `504` | le moteur a échoué — une tentative de plus est comptée |

**Un moteur vérifie ce qu'il rend.** Un fichier de la bonne durée peut ne
porter que du souffle : quatre campagnes de mesure l'ont montré sur ce projet.
Rendre du silence est un échec (`502`), pas un résultat.

**`config` vient de la route, jamais de la machine.** Le backend résout quelle
version de modèle exécute un travail et joint le réglage de sa route. C'est ce
qui permet à deux versions du même binaire de rendre deux résultats sans deux
images — et ce qui empêche un PC de choisir sa propre qualité.

## `GET /health`

Répond toujours, et dit la vérité sur le moteur — pas seulement sur le serveur
HTTP. Un agent qui rejoint la ferme déclare ce qu'il sait faire ; répondre sain
sans vérifier ferait entrer une machine qui échouera au premier travail réel.

```json
{"status": "ok", "engine": true, "enginePath": "deepfilternet3"}
```

## Les cinq opérations

### `POST /synthesize` — `TTS`

```json
{"text": "...", "language": "French", "instruction": "", "emotion": "",
 "preset_voice": "", "voice_sha256": "", "voice_b64": "<absent d'ordinaire>"}
```

Seule l'**empreinte** de la voix arrive : renvoyer 24 Mo à chaque segment d'un
chapitre serait absurde. Si la machine n'a pas ce profil, le moteur répond
`409 VOICE_NOT_CACHED` au lieu de deviner, l'agent va le chercher une fois et
rejoue avec `voice_b64`.

Rend `{"audio_b64", "format", "size_bytes", "engine"}`.

### `POST /enroll` — `VOICE_CLONE`

```json
{"reference_b64": "...", "voice_name": "...", "language": "French",
 "reference_text": "..."}
```

Rend `{"voice_b64", "sha256", "size_bytes"}`. L'empreinte rendue ne fait pas
autorité : **le backend la recalcule**. Accepter le mot de la machine
reviendrait à lui laisser nommer l'objet.

### `POST /design` — `VOICE_DESIGN`

```json
{"description": "...", "text": "...", "language": "French"}
```

Rend un extrait — `{"audio_b64", "format", "size_bytes"}` — et **jamais** un
profil durable. L'extrait validé devient ensuite l'échantillon d'un clonage.

### `POST /enhance` — `AUDIO_ENHANCE`

```json
{"audio_b64": "...", "config": {"atten_lim_db": 30}}
```

Rend `{"audio_b64", "format", "size_bytes", "engine"}`, plus les mesures que
l'agent remonte en télémétrie : `peak`, `silence_ratio`, `duration_seconds`.

Trois moteurs servent cette opération et ne se distinguent que par leur version
de modèle côté ABO. Le client demande `AUDIO_ENHANCE`, jamais « DeepFilterNet ».

| moteur | ce qu'il fait | où il tourne |
|---|---|---|
| `deepfilternet` | filtre le bruit, ne touche pas à la voix | processeur, RTF 0,25 |
| `clearervoice` | rehausse en 48 kHz, reste fidèle | GPU |
| `resemble_enhance` | **régénère** la parole | GPU, le plus lent |

Le troisième mérite un avertissement : il reconstruit la voix au lieu de la
filtrer, donc il peut s'écarter de ce qui a été enregistré. C'est un outil
différent des deux autres, pas une qualité supérieure.

**Le moteur normalise son entrée lui-même.** Les modèles travaillent en 48 kHz
mono ; une prise de téléphone en 16 kHz stéréo donnerait un résultat plausible
et faux — le genre de défaut qui ne se voit dans aucun format de fichier et ne
s'entend qu'à l'écoute.

### `POST /convert` — `PERFORMANCE_TRANSFER`

```json
{"audio_b64": "<la performance>", "reference_b64": "<le timbre>", "config": {}}
```

**Deux entrées de natures opposées, et les intervertir ne lève aucune erreur** :
le format serait valide, la durée juste, et le résultat serait la mauvaise voix.
`audio_b64` porte le jeu — le rythme, l'intention, les respirations —
`reference_b64` porte le timbre à lui prêter.

La référence est un **échantillon audio**, jamais un `.qvoice` : ce moteur ne
parle pas le format de Qwen. Quand le timbre vient d'une voix ABO, c'est son
échantillon d'origine qui part — précisément ce pour quoi `ADR-004` exige qu'il
survive à l'artefact.

Rend `{"audio_b64", "format", "size_bytes", "engine"}`.

## Ajouter un moteur

1. Écrire le serveur : `/health` et l'endpoint de son opération.
2. Le déclarer côté backend — une `model_version` et une `inference_route` en
   `execution = FARM`, dont l'`adapter_type` est la clé du moteur.
3. Le semer **caché** : `catalog_visibility = HIDDEN`, `serving_status =
   SERVING`. Le modèle existe et est relançable, il n'est proposé pour aucun
   nouveau travail.
4. L'éprouver sur une vraie machine, avec une vraie entrée, et **écouter** ce
   qui sort.
5. Le publier alors seulement :

```bash
docker compose -p abo_backend exec api \
  python -m app.registry.cli publish audio.deepfilternet 1 DEFAULT "ce qu'on a mesuré"
```

L'ordre compte. Publier d'abord, c'est annoncer un service qu'on n'a pas
entendu — le défaut exact que la refonte Android a trouvé sur
`PERFORMANCE_TRANSFER` et `AUDIO_ENHANCE`, dont les seules routes étaient un
bouchon `echo` qui rendait du vide.
