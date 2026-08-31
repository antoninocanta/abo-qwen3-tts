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

Depuis la console d'administration, créer un worker et relever son secret.
Il est propre à cette machine et révocable : révoquer un worker l'arrête au
pouls suivant, sans toucher aux autres.

## 3. Lancer l'agent

```bash
ABO_WORKER_SECRET=... docker compose -f deploy/docker-compose.yml up -d
```

L'agent se déclare, annonce ses moteurs et son matériel, puis attend du
travail. **Aucun port n'est ouvert** : tout part de la machine vers ABO.

## Retirer une machine

Passer le worker en `DRAINING` depuis la console : il finit ses jobs en cours
et n'en accepte plus. Attendre qu'il soit vide, puis arrêter les conteneurs.

Arracher une machine active n'est pas une faute grave — ses jobs repartent
ailleurs — mais c'est du travail refait pour rien.

## Ce qui reste sur la machine

Rien de durable. Un profil de voix, un échantillon, un texte transitent le
temps d'un job. Le cache de voix accélère les segments suivants ; il est
jetable, et le perdre ne coûte qu'un renvoi.
