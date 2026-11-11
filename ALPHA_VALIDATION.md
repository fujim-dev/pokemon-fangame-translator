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

## Rapport de progression : Pokémon Myth 2 v1.1 — 11 novembre 2026

**Statut : en cours de test ; extraction non encore validée.** Vérifications sur copie isolée uniquement, sous `Documents/PFT_ALPHA_TESTS`, sans publication d'assets de fangame.

- ZIP : 26 878 entrées, contrôle CRC intégral réussi. Détection PFT : Essentials 21.1, confiance 100/100, reconstruction bloquée.
- Première erreur d'extraction : un `Point` PBS de `town_map.txt` a un neuvième champ final vide ; correctif strict dans `essentials_town_map.py` (commit `d466774f`) et quatre tests (commit `5889c6ac`).
- Deuxième erreur : `trainers.txt` se termine sans CRLF, et 19 des 127 dresseurs ont un `LoseText` vide. Le compilé contient aussi 19 textes vides ; la banque runtime contient une clé vide. Correctifs dans `essentials_trainer.py` (commits `59e2b100`, `53d971bc`), tests ciblés (commits `a2e7685e`, `cfcaaca0`). La preuve croisée a réussi pour 127 dresseurs avec ces correctifs.
- Troisième tentative d'extraction : blocage désormais sur `PBS/abilities.dat/ABILITY_NAMES/ABILITY_DESCRIPTIONS`. **À diagnostiquer avant toute autre correction.**
- Contrôles SHA-256 avant/après des fichiers de référence `Game.ini` et `Data/Scripts.rxdata` : identiques. Les trois sources de dresseurs sont restées inchangées lors du diagnostic. Aucune reconstruction ou exécution du jeu.
- Tests ciblés sur PC : 6/6 réussis pour la fin de ligne et les valeurs `LoseText` vides. Le résultat de la CI complète doit être vérifié séparément.

Une détection réussie et des sous-étapes de preuve corrigées ne constituent **pas** une compatibilité confirmée.
