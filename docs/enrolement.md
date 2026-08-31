# Enrôler une machine

Trois étapes, dans cet ordre.

## 1. Préparer la machine

```bash
sudo ./deploy/install-ubuntu.sh
```

Installe Docker et le NVIDIA Container Toolkit, puis **vérifie que le GPU est
visible depuis un conteneur**. Sans cette vérification, la panne se
découvrirait au premier job, sur une machine déjà enrôlée et supposée saine.

## 2. Obtenir un secret d'enrôlement

Côté backend, créer la machine en ligne de commande :

```bash
docker compose -p abo_backend exec api python -m app.workers.cli create "PC du bureau" OWNED
```

Elle rend une **clé** et un **secret**, ce dernier affiché une seule fois. Le
secret est propre à cette machine et révocable : révoquer un worker l'arrête au
pouls suivant, sans toucher aux autres. Le perdre veut dire recréer le worker —
un secret qu'on peut relire est un secret qui traîne.

## 3. Lancer l'agent

```bash
export ABO_BACKEND_URL=https://…
export ABO_WORKER_KEY=wk_…
export ABO_WORKER_SECRET=…
docker compose -f deploy/docker-compose.yml up -d
```

L'agent se déclare, annonce ses moteurs et son matériel, puis attend du
travail. **Aucun port n'est ouvert** : tout part de la machine vers ABO.

## Retirer une machine

Passer le worker en `DRAINING` depuis la console — écran **Ferme**, motif
obligatoire : il finit ses jobs en cours et n'en accepte plus. Attendre que la
colonne « en cours » tombe à zéro, puis arrêter les conteneurs.

Arracher une machine active n'est pas une faute grave — ses jobs repartent
ailleurs — mais c'est du travail refait pour rien.

## Ce qui reste sur la machine

Rien de durable. Un profil de voix, un échantillon, un texte transitent le
temps d'un job. Le cache de voix accélère les segments suivants ; il est
jetable, et le perdre ne coûte qu'un renvoi.
