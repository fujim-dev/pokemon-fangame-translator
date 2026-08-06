# Instructions Codex — Pokémon Fangame Translator

## Priorité absolue

Avant toute modification, lire intégralement :

1. `PROJECT_CONTEXT.md`
2. `ARCHITECTURE.md`
3. `ROADMAP_V1.1.md`
4. `ACCEPTANCE_TESTS_V1.1.md`

Ne pas commencer une grosse implémentation tant que l'architecture actuelle, les risques et le plan de migration n'ont pas été résumés.

## Profil du projet

- Application Windows en Python/Tkinter.
- Public principal : utilisateurs non techniques.
- Version de référence : `v1.0.2 — Bêta publique`.
- Licence du projet : `GPL-3.0-or-later`.
- Le dépôt ne doit jamais contenir de fangame, de fichiers `Data`, de dialogues extraits, de médias ou d'autres contenus appartenant aux créateurs des jeux.

## Règles de sécurité non négociables

1. Ne jamais modifier le dossier original du fangame.
2. Toute reconstruction doit être réalisée dans une copie séparée.
3. Ne jamais exécuter du code Ruby inconnu pendant l'analyse.
4. Un format inconnu ou une détection incertaine doit bloquer la traduction et la reconstruction.
5. Toute réparation automatique doit être :
   - déterministe ;
   - documentée ;
   - réversible ;
   - limitée à la copie de travail.
6. Préserver les commandes techniques et les marqueurs de contrôle.
7. Relire les fichiers reconstruits avant de déclarer l'opération réussie.
8. Ne pas masquer les incertitudes : les signaler dans le rapport.
9. Ne pas annoncer qu'un jeu entier est « validé » à partir d'une analyse statique. Employer « validation analytique ».
10. Ne pas casser les fonctions stables de la v1.0.2.

## Méthode de travail attendue

Pour chaque tâche importante :

1. analyser les fichiers concernés ;
2. proposer un plan court ;
3. ajouter ou mettre à jour les tests ;
4. implémenter par petites étapes ;
5. exécuter les vérifications ;
6. résumer les fichiers modifiés, les tests et les limites restantes.

Éviter les réécritures massives sans besoin démontré. Préférer des adaptateurs et services testables à une accumulation de conditions dans l'interface Tkinter.

## Compatibilité et adaptateurs

La v1.1 doit tendre vers une architecture à adaptateurs :

- `PokemonEssentialsAdapter` pour les structures RPG Maker XP / Pokémon Essentials classiques ;
- `PokemonFluxAdapter` expérimental pour les structures Flux reconnues ;
- `UnknownAdapter` en lecture seule, sans traduction ni reconstruction.

La détection doit utiliser plusieurs indices structurels. Le nom du dossier ou du jeu ne suffit jamais.

L'interface ne doit afficher ou activer que les actions compatibles avec l'adaptateur sélectionné automatiquement.

## Analyse profonde

L'analyse profonde est statique et ne doit pas lancer le jeu ni exécuter ses scripts. Elle peut :

- relire les fichiers compatibles ;
- analyser toutes les cartes et pages d'événements accessibles ;
- vérifier les références statiques ;
- calculer la couverture française estimée ;
- signaler les scripts dynamiques non vérifiables ;
- produire un rapport partageable.

## Réparations automatiques

N'autoriser automatiquement que des réparations sûres et connues, par exemple :

- restauration de commandes protégées simples ;
- réécriture atomique d'un fichier invalide à partir de la copie de travail ;
- correction d'encodage lorsque la transformation est non ambiguë ;
- restauration depuis le point de sauvegarde.

Pour un cas ambigu, ne pas improviser : conserver la valeur originale et produire une alerte.

## Vérifications minimales

Après toute modification Python :

```text
python build_support/verify_sources.py
python -m py_compile Pokemon_Fangame_Translator.py translation_studio.py reconstruction_studio.py reconstruction_engine.py structured_extractor.py ruby_marshal_reader.py ruby_marshal_writer.py
```

Lorsqu'une suite de tests existe :

```text
python -m unittest discover -s tests -v
```

Ne pas déclarer le travail terminé si les tests ou vérifications échouent.

## Conventions

- Python typé lorsque cela améliore la clarté.
- Encodage UTF-8 explicite.
- Écritures atomiques pour les fichiers importants.
- Journaux compréhensibles par un débutant.
- Messages utilisateur en français.
- Les détails techniques peuvent être placés dans les rapports.
- Aucun secret, jeton, chemin personnel ou ressource de fangame dans les commits.
