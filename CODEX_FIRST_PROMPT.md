# Premier message à envoyer à Codex

Copier-coller le message ci-dessous dans Codex après avoir connecté le dépôt.

---

Lis entièrement `AGENTS.md`, `PROJECT_CONTEXT.md`, `ARCHITECTURE.md`, `ROADMAP_V1.1.md` et `ACCEPTANCE_TESTS_V1.1.md`.

Analyse ensuite tout le dépôt actuel de Pokémon Fangame Translator v1.0.2.

Pour cette première tâche, ne modifie aucun fichier.

Produis seulement :

1. un résumé fidèle de l'architecture actuelle ;
2. la liste des risques techniques et des dettes qui concernent la v1.1 ;
3. une comparaison entre l'architecture actuelle et l'architecture cible proposée ;
4. un plan d'implémentation découpé en petites pull requests ;
5. les premiers tests automatisés à écrire avant toute refactorisation ;
6. les questions ou informations réellement manquantes.

Contraintes absolues :

- ne jamais modifier le fangame original ;
- ne jamais exécuter de code Ruby inconnu ;
- bloquer toute reconstruction lorsque la détection est incertaine ;
- conserver la compatibilité v1.0.2 ;
- ne pas ajouter de fichier ou contenu provenant d'un fangame ;
- ne pas commencer l'adaptateur Flux avant que les garde-fous et tests de base soient en place.

Présente ta réponse en français, avec des termes compréhensibles pour un propriétaire de projet non technique.

---

## Deuxième tâche conseillée, après validation du plan

Après avoir lu et validé le premier rapport de Codex, envoyer :

```text
Implémente uniquement la Phase 0 de ROADMAP_V1.1.md.

Commence par créer une suite de tests artificiels qui couvre :
- la compilation des sources ;
- merge_project_rows ;
- les commandes protégées ;
- les identifiants stables ;
- un round-trip minimal Ruby Marshal ;
- la sécurité de simulation/reconstruction sur une fixture artificielle.

Ne refactorise pas encore l'interface et n'ajoute pas encore Flux.

Exécute tous les tests et résume précisément les modifications et les limites.
```
