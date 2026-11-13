# Fiche d'exécution — validation Alpha Essentials

**Statut : À EXÉCUTER.** Cette fiche ne constitue pas un résultat de test.

## Cible initiale

Pokémon Myth 2, version exacte à relever sur la copie de référence. La documentation actuelle mentionne un essai antérieur de traduction et de lancement, mais celui-ci doit être **reproduit sur la branche actuelle**. Un projet synthétique Essentials v21.1 sert d'abord à vérifier le protocole.

## Préparation sans risque

- Ne jamais utiliser le Bureau Windows comme espace de travail ; ne rien modifier sur le Bureau.
- Vérifier la provenance et l'autorisation d'utilisation de l'exemplaire.
- Décompresser ou copier le jeu dans un nouveau dossier de test dédié, hors des originaux.
- Prévoir un dossier de sortie distinct, inexistant au départ ; ne jamais utiliser le chemin de la source.
- Relever version de PFT, commit, nom et version du jeu, empreintes SHA-256 des fichiers source pertinents.
- Conserver les sauvegardes personnelles hors du périmètre de test.

## Contrôles à cocher par l'opérateur

- [ ] Le moteur et ses limites sont correctement affichés.
- [ ] L'analyse ne modifie aucun fichier source.
- [ ] L'extraction aboutit sans exécuter de scripts inconnus.
- [ ] Les chaînes exportées sont vérifiables et leurs commandes protégées intactes.
- [ ] Quelques traductions de contrôle sont acceptées, notamment accents et variables.
- [ ] La simulation de reconstruction ne signale aucun conflit.
- [ ] La reconstruction produit une **copie séparée**, sans toucher à la source.
- [ ] Les empreintes des fichiers originaux sont identiques après reconstruction.
- [ ] La copie reconstruite démarre ; plusieurs dialogues et menus sont vérifiés.
- [ ] Une seconde exécution et les erreurs de sortie existante sont gérées sans écrasement.
- [ ] Les limites et éventuels échecs sont consignés, avec étapes reproductibles.

## Rapport d'exécution

| Champ | À renseigner |
|---|---|
| Date et opérateur | |
| Jeu et version exacte | |
| PFT commit / version | |
| Dossier de travail isolé (hors Bureau) | |
| Moteur détecté et confiance | |
| Extraction (réussite / échec / non tentée) | |
| Traduction test (réussite / échec / non tentée) | |
| Reconstruction (réussite / échec / non tentée) | |
| Source inchangée (empreintes avant/après) | |
| Lancement de la copie | |
| Limitations et bugs | |
| Statut final | En cours de test |

**Règle :** aucun statut « compatible confirmé » sans résultats enregistrés pour l'ensemble du parcours applicable. Ne pas publier de fichiers protégés ou de sauvegardes.
