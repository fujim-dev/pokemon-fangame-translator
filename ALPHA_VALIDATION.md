# Validation de la première Alpha PFT

Ce document est un **plan de test**, pas une annonce de compatibilité générale.

## Critères pour annoncer un jeu « compatible confirmé »

Sur une **copie isolée** du jeu, avec version exacte consignée :
1. Détection du moteur et de sa version, sans exécution de scripts inconnus.
2. Analyse des données et extraction des textes pris en charge.
3. Traduction d'un petit échantillon avec accents, variables et commandes préservés.
4. Reconstruction dans un **nouveau dossier**, sans écraser le jeu ni ses sauvegardes.
5. Lancement de cette copie et vérification en jeu des dialogues modifiés.
6. Documentation des limites : textes non extraits, plugins, cartes, événements ou bugs.

Un test automatisé réussi **ne suffit pas** à déclarer un fangame compatible.

## Candidats pour la batterie de tests

| Jeu / projet | Statut | Ce qui reste à vérifier |
|---|---|---|
| Pokémon Myth 2 — version testée précédemment | Validation ciblée antérieure ; **à revalider sur la branche actuelle** | Refaire le parcours complet sur copie et consigner la version exacte |
| Projet de référence Pokémon Essentials v21.1 | Tests automatisés ciblés ; **à tester de bout en bout** | Extraction, reconstruction et lancement sur copie |
| Pokémon Flux Episode 2 v2.1.0 | **Expérimental : non compatible confirmé** | Extraction ciblée ; reconstruction actuellement désactivée |
| Pokémon Studio / PSDK Technical Demo | **Reconnaissance seule** | Extraction et reconstruction non implémentées ; ne pas annoncer compatible |
| Pokémon Void 0.1.5 (archive chiffrée) | **Non validé** | Ne pas forcer l'extraction ni déchiffrer sans autorisation |

## Liste publique

- **Compatible confirmé** : parcours complet validé sur la version indiquée.
- **Compatibilité partielle** : certaines étapes fonctionnent, limites explicites.
- **En cours de test** : résultat insuffisant pour conclure.
- **Non pris en charge** : fonction requise absente ou bloquée.

Ne jamais transformer « moteur détecté » en « jeu compatible ».

## Retours de la communauté

Demander : nom et version du jeu, version de PFT, moteur si connu, fonction essayée, résultat attendu, résultat obtenu, étapes de reproduction, message d'erreur expurgé. Ne pas demander de fichiers de jeu protégés ni de sauvegardes personnelles. Confirmer les résultats reproductibles avant de modifier la liste.

## Sécurité et livraison

Ne jamais toucher aux originaux, au Bureau Windows, ni aux sauvegardes. Utiliser des copies de travail dans un emplacement dédié, des données synthétiques pour les tests automatisés, et garder la branche `main` intacte jusqu'à revue et validation.
