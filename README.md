# Pokémon Fangame Translator

**Version 1.0.2 — Bêta publique**  
Projet open source distribué sous licence **GPL-3.0-or-later**.

Pokémon Fangame Translator aide un utilisateur non technique à extraire, traduire et reconstruire une **copie française séparée** de certains fangames RPG Maker XP / Pokémon Essentials compatibles.

> **Avertissement :** la traduction est automatique. Elle peut contenir des erreurs et ne remplace pas le travail d'une équipe de traduction humaine. Relisez les passages importants.
 ## Discord officiel

Rejoignez la communauté pour obtenir de l’aide, signaler un bug, proposer une amélioration ou partager vos tests de compatibilité.

➡️ [Rejoindre Pokémon Fangame Translator — Communauté FR](https://discord.gg/jna3acM7Wy)

## Installation Windows

1. Téléchargez `Pokemon_Fangame_Translator_Setup_v1.0.2.exe` depuis la page officielle du projet.
2. Lancez l'installateur.
3. Ouvrez l'application depuis le Bureau ou le menu Démarrer.

Les utilisateurs n'ont besoin ni de Python, ni de PyInstaller, ni d'Inno Setup. Une connexion Internet est nécessaire une fois pour télécharger le modèle anglais → français d'Argos Translate.

## Parcours débutant

1. Choisir le dossier complet du fangame.
2. Analyser puis extraire les textes.
3. Traduire un lot de 20 pour commencer.
4. Vérifier les alertes et accepter les textes prêts.
5. Ouvrir Reconstruction et lancer la simulation.
6. Créer la `VERSION_FR`.
7. Jouer dans cette copie et conserver le jeu original intact.

## Sécurité

- Le dossier original n'est jamais modifié.
- `Scripts.rxdata`, `PluginScripts.rxdata` et les scripts Ruby sont exclus.
- Les commandes `\n`, `\c[]`, `\PN`, `\v[]` et autres marqueurs sont contrôlées.
- Les textes incompatibles restent en anglais par sécurité.
- La reconstruction écrit uniquement dans une copie séparée.
- Les diagnostics publics n'exportent aucun dialogue ni traduction.

## Compatibilité

Compatibilité actuellement validée principalement avec **Pokémon Myth 2**. Le projet vise certains fangames RPG Maker XP / Pokémon Essentials dont les dossiers `Data` et `PBS` sont accessibles. Les archives chiffrées, moteurs personnalisés et textes stockés dans les scripts peuvent ne pas être pris en charge.

Pokémon Flux Episode 2 v2.1.0 peut désormais être détecté et analysé
statiquement par un adaptateur expérimental séparé. Pour cette signature exacte,
l'extraction des occurrences vérifiables vers le CSV commun est autorisée en
lecture seule ; les versions inconnues restent bloquées. Les chaînes ambiguës ou
dynamiques sont conservées sans interprétation. L'import dans le FPK et la
reconstruction Flux restent désactivés tant que le chemin d'écriture n'a pas été
validé de bout en bout sur une copie de travail.

## Projet indépendant

Ce projet est indépendant et non officiel. Il n'est affilié ni à Nintendo, Game Freak, The Pokémon Company, ni aux créateurs des fangames. Aucun fangame, graphisme, musique, carte, dialogue extrait ou autre fichier de jeu n'est inclus.

## Signaler un bug

Utilisez le bouton **Exporter un diagnostic** dans l'application, puis joignez ce fichier au ticket. Ne publiez pas le fangame complet ni un CSV contenant les dialogues.

## Contribuer

Les corrections et améliorations sont bienvenues. Consultez [CONTRIBUTING.md](CONTRIBUTING.md). Toute redistribution du logiciel modifié doit respecter la GPL-3.0-or-later et les licences des composants tiers.
## Documents du projet

- [Compatibilité et limites connues](COMPATIBILITY.md)
- [Contribuer](CONTRIBUTING.md)
- [Code de conduite](CODE_OF_CONDUCT.md)
- [Sécurité](SECURITY.md)
- [Confidentialité](PRIVACY.md)
- [Composants tiers](THIRD_PARTY_NOTICES.md)
