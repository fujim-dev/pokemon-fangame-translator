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

## Revalidation ciblée du parcours v21.1 — 11 novembre 2026

Exécution sur Windows / Python 3.13, avec `test_essentials_v21.EssentialsV21ProfileTests`, dans un projet de jeu synthétique placé en dossier temporaire. L'extraction et la reconstruction sont exécutées via les composants existants de PFT, sans autoriser le mode général de reconstruction pour les jeux v21.1 en lecture seule.

**Tests positifs (3/3 réussis) :**
- `test_v21_map_dialogue_choice_roundtrip_updates_matching_402_only` : dialogue et choix, correspondance des branches 102/402, reconstruction en copie, relecture des traductions et préservation des commandes non ciblées.
- `test_v21_private_validation_roundtrip_is_limited_to_one_message_bank_row` : modification unique de banque runtime, manifeste de provenance, réextraction et intégrité des autres entrées.
- `test_v21_private_point_roundtrip_changes_only_the_target_subfield` : modification corrélée du PBS et de `town_map.dat`, preuve compilée, comparaison des fichiers non ciblés.

**Tests négatifs (4/4 réussis) :**
- Refus d'une branche de choix 402 ambiguë.
- Refus d'une preuve de segmentation absente ou altérée.
- Refus si la provenance ou la source change après préparation du plan.
- Refus de types et de multiples lignes hors du périmètre validé.

**Note d'exécution :** un premier lanceur sélectif écrit ad hoc sans garde `if __name__ == "__main__"` a échoué sur Windows pendant l'initialisation du détecteur en processus isolé (`ProbeIsolationError`). Le lanceur a été corrigé avec cette garde, puis les sept tests ont réussi (3/3 et 4/4). Le lanceur initial ne prouve aucun bug du moteur.

La suite complète `unittest discover` avait également réussi sur ce PC : **370 tests, aucun échec, un ignoré**. Aucun nouveau pourcentage de compatibilité ne doit être inféré de ces vérifications. La copie réelle de Pokémon Myth 2 v1.1 reste bloquée par des banques Ability incomplètes ; aucune reconstruction réelle du jeu n'a été entreprise.

## Correction du diagnostic Myth 2 — 11 novembre 2026 (après examen des scripts Ruby)

**Important : les mentions antérieures de 111 textes de capacités « manquants » doivent être corrigées.** L'analyse de `Data/Scripts.rxdata` a établi que `GameData::Ability#name` et `#description` appellent `pbGetMessageFromHash`. Le moteur recherche les messages dans la banque du jeu avant celle du système et retourne le texte source lorsque la clé est absente. Les index `ABILITY_NAMES = 10` et `ABILITY_DESCRIPTIONS = 11` sont confirmés.

Sur la copie isolée Myth 2 v1.1 :
- `messages_core.dat` : 266 noms et 252 descriptions distinctes.
- `messages_game.dat` : 55 noms et 56 descriptions supplémentaires.
- Total combiné : **321 noms et 308 descriptions distinctes**, soit exactement les sources PBS/compilées. Il ne manque **aucun** de ces 111 textes.
- Les fichiers compilés `abilities.dat` et PBS possèdent 325 capacités cohérentes. Les fichiers du jeu restent inchangés.

Le diagnostic `analysis/ability_runtime_diagnostic.py` reconnaît désormais la répartition *core + game* uniquement si elle est structurellement exacte. Il expose `split_complete=true` et `missing_unique=0`. L'analyse approfondie émet `ability_runtime_split_unverified` **bloquante**, non `ability_runtime_incomplete`, parce que **l'extracteur et le reconstructeur restent limités à la banque core** et n'ont pas encore de preuve supportant les deux couches. Les entrées de l'UI sont également bloquées jusqu'à validation. Le module expérimental de réparation en mémoire détecte ce cas et retourne `reason=already_in_game_bank` : ne plus proposer de rajouter ces entrées dans `messages_core.dat`.

Tests synthétiques : 8/8 diagnostics et 11/11 protections UI réussis localement. Cette correction ne vaut pas compatibilité totale de Myth 2 ; l'extraction complète et la reconstruction restent à valider après adaptation sûre des chemins de preuve *core + game*. Aucun jeu, sauvegarde ni script Ruby exécuté ou modifié.

## Suite Myth 2 : capacité core/jeu, Pokédex et audit de non-régression — 11 novembre 2026

**Aucune extraction complète ni reconstruction de Myth 2 n'est homologuée.** Sur copie isolée, le nouveau module `essentials_ability_split.py` construit 650 preuves en lecture seule (535 provenant de `messages_core.dat` et 115 de `messages_game.dat`) pour 325 capacités. Les preuves de type `pft_v21_1_ability_split_runtime_readonly_v1` contiennent le chemin et la SHA-256 de la banque physique, et excluent explicitement la reconstruction. Le test d'extraction réel franchit maintenant ce blocage et atteint les contrôles des espèces.

L'examen du Pokédex a établi :
- Les PBS `pokemon.txt` et `pokemon_forms.txt` peuvent être **sans BOM**, tout en restant UTF-8/CRLF. Le parseur et les preuves préservent désormais le format exact, y compris pendant le round-trip synthétique.
- `species.dat` comporte 1 389 objets avec les deux attributs supplémentaires `@raid_ranks` et `@raid_style` (listes, en fin d'objet) : ce profil précis est validé, les champs inconnus sont toujours refusés.
- Neuf entrées Pokédex PBS comportent un commentaire final de forme ` #1.`, ` #2.` ou ` #3.` ; la fonction Ruby `prepline` supprime les commentaires `#` avant compilation. PFT conserve ces suffixes dans le PBS, hors du texte à traduire et liés par les empreintes.
- Le nouveau blocage est **la banque Pokédex `POKEDEX_ENTRIES` (index 3)** : 1 138 textes compilés distincts sont tous couverts par l'union de `messages_core.dat` et `messages_game.dat`. Core contient 1 028 entrées dont quatre ne correspondent plus au jeu compilé, game en contient 114, sans chevauchement. L'ordre combiné ne correspond pas exactement à l'ordre compilé. Il ne faut ni concaténer aveuglément ni supprimer les quatre entrées historiques. Une preuve par clé et source physique est à concevoir et à tester.
- Fichiers Myth 2 inchangés, tests synthétiques sur copies temporaires uniquement.

La CI GitHub a exposé une **régression accidentelle** sur le module Phone (`actual_runtime_source` utilisé hors de son contexte), corrigée sur la branche au commit `3c9bf0a1`. Les anciens tests qui refusaient la suppression du BOM ont été adaptés à la nouvelle exigence de format (sans affaiblir les contrôles CRLF). La validation Windows locale a repassé 39 tests ciblés et 37 tests de détection, tous réussis. Les runs de CI complets des nouveaux commits restent à confirmer : ne pas les annoncer comme verts avant confirmation.

Prochain travail : sécuriser et tester la corrélation `POKEDEX_ENTRIES` sur les deux couches sans s'appuyer sur leur ordre concaténé, puis reprendre l'extraction certifiée sur copie isolée, avec les protections de reconstruction maintenues.
