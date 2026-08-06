# Contexte du projet — Pokémon Fangame Translator

## 1. Mission

Pokémon Fangame Translator est une application Windows destinée à aider un utilisateur non technique à :

1. sélectionner le dossier complet d'un fangame ;
2. analyser sa structure ;
3. extraire les textes compatibles ;
4. traduire de l'anglais vers le français ;
5. relire et corriger les résultats ;
6. reconstruire une copie française séparée ;
7. conserver le jeu original intact.

Le logiciel ne fournit aucun fangame et n'est affilié ni à Nintendo, Game Freak, The Pokémon Company, ni aux équipes créatrices des fangames.

## 2. Version actuelle de référence

La base de travail est la `v1.0.2 — Bêta publique`.

Fonctions déjà disponibles :

- interface Tkinter pensée pour les débutants ;
- sélection du dossier complet d'un fangame ;
- diagnostic heuristique RPG Maker XP / Pokémon Essentials ;
- extraction structurée des cartes `.rxdata`, banques `.dat` compatibles et fichiers PBS ;
- CSV de projet persistant dans le dossier Documents de l'utilisateur ;
- conservation des traductions lors d'une nouvelle extraction ;
- studio de traduction avec Argos Translate anglais → français ;
- glossaire et mémoire de corrections ;
- protection et vérification des commandes techniques ;
- détection de doublons ;
- niveaux de relecture ;
- simulation de reconstruction ;
- reconstruction atomique dans une copie séparée ;
- validation après reconstruction ;
- rapport de diagnostic public sans dialogues ni chemins personnels complets ;
- installateur Windows construit avec PyInstaller et Inno Setup.

## 3. Public visé

Le public principal n'est pas développeur. Les choix d'interface doivent donc :

- éviter le jargon lorsqu'il n'est pas indispensable ;
- empêcher les mauvaises actions plutôt que seulement les expliquer ;
- afficher des statuts simples ;
- proposer des rapports compréhensibles ;
- conserver un mode détaillé pour les développeurs.

## 4. Expérience acquise

### Pokémon Myth 2 / Essentials classique

La chaîne extraction → traduction → reconstruction a été testée avec succès sur un fangame de type RPG Maker XP / Pokémon Essentials. Une ancienne reconstruction a rencontré une erreur d'encodage UTF-8, ensuite corrigée en ajoutant des contrôles avant écriture.

### Pokémon Flux

Pokémon Flux a nécessité un outil séparé et des manipulations spécifiques autour d'une archive FPK personnalisée. Cette expérience a montré qu'un moteur générique ne doit pas tenter de traiter tous les formats avec le même chemin de reconstruction.

La prise en charge Flux doit devenir un adaptateur dédié, expérimental et verrouillé sur les versions réellement reconnues.

## 5. Décisions produit pour la v1.1

La v1.1 doit introduire une détection automatique du profil de jeu.

Parcours cible :

```text
Sélection du dossier
→ détection du moteur et de la structure
→ choix automatique de l'adaptateur
→ affichage des seules fonctions compatibles
→ traduction
→ reconstruction sécurisée
→ analyse profonde
→ rapport
```

Exemples :

- Structure Essentials classique détectée : fonctions Data/PBS disponibles, fonctions Flux masquées.
- Structure Flux reconnue : fonctions Flux disponibles, fonctions PBS classiques masquées.
- Structure inconnue ou confiance insuffisante : analyse en lecture seule uniquement ; traduction et reconstruction bloquées.

## 6. Validation analytique profonde

L'objectif n'est pas de faire jouer automatiquement le jeu.

L'application doit pouvoir analyser statiquement le plus de contenu possible sans lancer le moteur :

- toutes les cartes lisibles ;
- toutes les pages d'événements ;
- dialogues et choix ;
- événements communs ;
- transferts de cartes statiquement détectables ;
- références statiques aux ressources ;
- banques de messages ;
- données PBS ;
- encodages ;
- commandes techniques ;
- fichiers manquants ou illisibles ;
- scripts dynamiques présents, sans les exécuter.

Le rapport doit indiquer précisément ce qui a été analysé, ignoré ou non vérifiable.

## 7. Couverture de traduction

La v1.1 doit calculer une couverture française estimée, en distinguant autant que possible :

- textes traduits ;
- textes encore probablement anglais ;
- textes partiellement traduits ;
- noms propres ou termes ambigus ;
- commandes techniques exclues ;
- textes conservés en anglais par sécurité ;
- fichiers ou structures non analysables.

Le résultat ne doit jamais être présenté comme une preuve que l'aventure entière a été jouée.

Formulation recommandée :

> Validation analytique approfondie réussie. Toutes les données statiquement accessibles ont été contrôlées. L'aventure complète n'a pas été jouée physiquement de bout en bout.

## 8. Diagnostic et réparation assistée

La v1.1 doit réduire les demandes de support répétitives en détectant et réparant automatiquement les anomalies connues et sans ambiguïté.

Exemples de réparations sûres :

- restaurer certaines commandes protégées simples ;
- corriger un encodage connu ;
- reconstruire à nouveau un fichier dont la validation a échoué ;
- restaurer la copie précédente ;
- conserver le texte original lorsqu'une traduction est dangereuse.

Une réparation ambiguë doit être refusée et documentée.

## 9. Contraintes juridiques et de confidentialité

Le dépôt, les tests et les rapports publics ne doivent inclure :

- aucun fangame complet ;
- aucun fichier propriétaire de jeu ;
- aucune banque de dialogues extraite ;
- aucun média de jeu ;
- aucune donnée personnelle ;
- aucun chemin utilisateur complet dans les diagnostics publics.

Les tests doivent utiliser des fixtures artificielles minimales créées pour le projet.

## 10. Définition d'une réussite

La v1.1 est réussie si :

- la v1.0.2 continue de fonctionner pour les jeux Essentials classiques ;
- le mauvais adaptateur ne peut pas être utilisé ;
- les formats inconnus sont bloqués en écriture ;
- l'original reste inchangé ;
- l'analyse profonde produit un rapport honnête ;
- les réparations automatiques sont réversibles ;
- les tests couvrent les cas de sécurité les plus importants.
