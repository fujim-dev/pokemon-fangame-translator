# Feuille de route : migration progressive de PFT vers Rust

**État : planification.** La migration n'est pas commencée ; le cœur actuel reste en Python.

## Pourquoi Rust ?

Étudier Rust pour les composants où la robustesse, la vitesse et la distribution Windows peuvent être améliorées. Ces bénéfices doivent être **mesurés** : Rust ne garantit pas à lui seul de meilleures performances.

## Principes non négociables

- Conserver le comportement fonctionnel de PFT et ses formats de données pendant la transition.
- Préserver les jeux originaux et les sauvegardes ; n'écrire que dans des copies isolées.
- Ne pas réécrire tous les moteurs en une seule fois.
- Garder les adaptateurs Essentials, Flux et Studio/PSDK distincts.
- Comparer chaque implémentation Rust aux tests Python existants, sur données **synthétiques**.
- Garder `main` inchangée jusqu'à revue et validation.

## Étapes proposées

1. **Stabiliser les contrats Python** : détection, capacités, extraction, reconstruction et erreurs documentées ; tests Windows verts.
2. **Valider un parcours Alpha complet** sur copie isolée, en commençant par les profils Essentials déjà les mieux couverts.
3. **Mesurer les goulots d'étranglement** : durée, mémoire, taille des données ; ne migrer que ce qui en bénéficie.
4. **Créer un petit module Rust autonome** pour une fonction pure (sans écriture sur disque), avec fixtures et résultats de référence Python.
5. **Brancher Rust derrière une interface stable** (processus séparé ou bibliothèque native, choix après prototype) ; garder un chemin Python de repli.
6. **Étendre par composants**, avec comparaison de résultats et tests de non-régression Windows à chaque étape.
7. **Préparer le packaging Windows** : exécutable, messages d'erreur compréhensibles, documentation et tests d'installation.

## Conditions pour migrer un composant

- Tests unitaires et d'intégration reproductibles.
- Même sortie que Python sur les cas de référence, sauf changement explicitement documenté.
- Aucun accès en écriture aux sources.
- Mesures avant/après si l'objectif est la performance.
- Possibilité de revenir à Python sans perte de données.

## Ce qui n'est pas promis pour l'Alpha

La première Alpha peut rester en Python. La migration Rust ne doit pas retarder les tests de compatibilité, ni faire annoncer PSDK/Flux comme traduisibles avant validation réelle.
