# Installer ce dossier de transfert dans le dépôt GitHub

## Contenu à ajouter

Les six fichiers suivants doivent être placés directement à la racine du dépôt `pokemon-fangame-translator`, au même niveau que `README.md` et `Pokemon_Fangame_Translator.py` :

```text
AGENTS.md
PROJECT_CONTEXT.md
ARCHITECTURE.md
ROADMAP_V1.1.md
ACCEPTANCE_TESTS_V1.1.md
CODEX_FIRST_PROMPT.md
```

## Méthode simple avec le site GitHub

1. Ouvrir le dépôt `pokemon-fangame-translator`.
2. Rester dans l'onglet `Code`.
3. Cliquer sur `Add file`.
4. Cliquer sur `Upload files`.
5. Ouvrir le dossier décompressé `PFT_Codex_Transfer_v1.1`.
6. Sélectionner uniquement les six fichiers `.md` listés ci-dessus.
7. Les faire glisser dans la page GitHub.
8. Vérifier que les noms apparaissent sans sous-dossier supplémentaire.
9. Dans `Commit message`, écrire :

```text
Ajout du contexte et de la roadmap Codex pour la v1.1
```

10. Conserver `Commit directly to the main branch`.
11. Cliquer sur `Commit changes`.

`README_IMPORT_CODEX.md` peut également être publié, mais il est surtout destiné au propriétaire du dépôt et n'est pas nécessaire au fonctionnement de Codex.

## Vérification sur GitHub

À la racine du dépôt, les fichiers doivent apparaître ainsi :

```text
AGENTS.md
PROJECT_CONTEXT.md
ARCHITECTURE.md
ROADMAP_V1.1.md
ACCEPTANCE_TESTS_V1.1.md
CODEX_FIRST_PROMPT.md
README.md
Pokemon_Fangame_Translator.py
...
```

Ils ne doivent pas être placés dans `assets`, `build_support` ou `.github`.

## Démarrer dans Codex

1. Ouvrir Codex.
2. Connecter le compte GitHub si nécessaire.
3. Sélectionner le dépôt `fujim-dev/pokemon-fangame-translator`.
4. Ouvrir `CODEX_FIRST_PROMPT.md`.
5. Copier le premier message.
6. Le coller dans une nouvelle tâche Codex.
7. Laisser Codex analyser le dépôt sans modifier les fichiers lors de cette première tâche.
8. Lire son plan avant d'autoriser du code.

## Important à propos de `/init`

Le dépôt contient déjà un `AGENTS.md` adapté au projet. Ne pas lancer `/init` en remplaçant ce fichier. Si `/init` est utilisé, comparer le résultat et fusionner manuellement les éventuels ajouts utiles au lieu d'écraser les consignes existantes.
