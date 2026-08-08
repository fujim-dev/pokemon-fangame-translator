# Roadmap v1.1 — Détection, validation analytique et réparation assistée

## Principe de livraison

La v1.1 doit être développée par étapes courtes et testables. Ne pas implémenter toutes les fonctions en une seule modification.

Chaque phase doit conserver la possibilité de revenir à la v1.0.2 et ne doit jamais réduire la sécurité de reconstruction.

## Phase 0 — Stabiliser la base

### Objectifs

- créer une suite de tests minimale ;
- figer les comportements importants de la v1.0.2 ;
- documenter les formats de CSV et de plans ;
- supprimer des artefacts de compilation du dépôt si nécessaire.

### Livrables

- dossier `tests/` ;
- tests de compilation ;
- tests des commandes protégées ;
- tests des identifiants stables ;
- tests du round-trip Ruby Marshal ;
- tests de simulation de reconstruction sur fixtures artificielles.

### Condition de sortie

Les fonctions existantes peuvent être modifiées avec un filet de sécurité automatisé.

## Phase 1 — Modèle d'adaptateurs et détection

### Objectifs

- créer le contrat `GameAdapter` ;
- créer `AdapterRegistry` ;
- créer `PokemonEssentialsAdapter` autour du code actuel ;
- créer `UnknownAdapter` ;
- retourner une détection argumentée.

### Règles

- plusieurs indices obligatoires ;
- le nom du dossier n'est jamais une preuve suffisante ;
- une égalité ou une confiance faible donne `UnknownAdapter` ;
- aucune action d'écriture pendant la détection.

### Interface

Après sélection :

```text
Profil détecté : Pokémon Essentials classique
Confiance : élevée
Actions disponibles : analyser, extraire, traduire, reconstruire
```

ou :

```text
Structure inconnue
Actions disponibles : inventaire et rapport en lecture seule
Traduction et reconstruction désactivées
```

### Condition de sortie

Le chemin Essentials de la v1.0.2 fonctionne à travers l'adaptateur et les formats inconnus sont bloqués.

## Phase 2 — Interface pilotée par capacités

### Objectifs

- ne plus afficher tous les boutons de façon identique ;
- activer ou masquer les fonctions selon les capacités de l'adaptateur ;
- afficher clairement l'adaptateur et sa version.

### Capacités suggérées

- `CAN_ANALYZE`
- `CAN_EXTRACT`
- `CAN_TRANSLATE`
- `CAN_RECONSTRUCT`
- `CAN_DEEP_ANALYZE`
- `CAN_AUTO_REPAIR`
- `EXPERIMENTAL`

### Condition de sortie

Il est impossible d'utiliser les fonctions Essentials avec un profil Flux ou inconnu.

## Phase 3 — Analyse profonde statique

### Objectifs

Analyser sans lancer le jeu ni exécuter de Ruby inconnu :

- inventaire complet ;
- relecture de tous les fichiers supportés ;
- toutes les cartes et pages d'événements compatibles ;
- événements communs ;
- transferts statiques ;
- références statiques de ressources ;
- banques de messages ;
- PBS ;
- encodages ;
- commandes protégées ;
- scripts dynamiques signalés.

### Modes

- Rapide : inventaire et contrôles principaux.
- Complet : toutes les données supportées.
- Approfondi : davantage de branches et de références, avec limite de temps configurable.

### Rapport

Produire au minimum :

- `.txt` lisible ;
- `.json` exploitable par le logiciel ;
- résumé copiable pour Discord.

### Condition de sortie

Le rapport distingue clairement les éléments vérifiés, non vérifiés et non supportés.

## Phase 4 — Couverture française estimée

### Objectifs

Calculer plusieurs métriques :

- couverture par lignes ;
- couverture par mots ;
- couverture par caractères ;
- textes probablement anglais ;
- textes mixtes ;
- textes ambigus ;
- textes exclus ;
- textes conservés pour sécurité.

### Règles

- les commandes techniques ne comptent pas comme anglais ;
- les noms courts et termes propres doivent être classés comme ambigus ;
- fournir la méthode de calcul dans le rapport ;
- ne pas afficher « 100 % traduit » si des structures ne sont pas analysables.

### Condition de sortie

La métrique est reproductible et accompagnée d'une marge ou d'un niveau de confiance.

## Phase 5 — Contrôle d'intégrité et réparation assistée

### Objectifs

Avant reconstruction :

- empreinte de l'original ;
- copie de travail ;
- point de restauration.

Après reconstruction :

- relecture des fichiers ;
- comparaison des structures ;
- vérification des commandes ;
- contrôle d'encodage ;
- détection des fichiers manquants ou vides ;
- comparaison des fichiers qui ne devaient pas changer.

### Boutons

- `Analyser en profondeur`
- `Réparer les problèmes sûrs`
- `Restaurer la copie précédente`
- `Exporter le rapport`

### Réparations autorisées

Seulement les corrections déterministes et réversibles.

### Condition de sortie

Une validation échouée provoque un rollback ou laisse la copie marquée invalide ; jamais un faux succès.

## Phase 6 — Rapport de compatibilité communautaire

### Objectifs

Créer un rapport partageable contenant :

- jeu et version détectés ;
- adaptateur ;
- version de l'application ;
- niveau de confiance ;
- fichiers/cartes/événements analysés ;
- couverture française ;
- alertes ;
- statut ;
- mention « aventure complète non jouée ».

### Statuts

- Vert : validation analytique approfondie réussie.
- Jaune : validation partielle ou éléments dynamiques non vérifiables.
- Rouge : erreur bloquante ou corruption détectée.

### Condition de sortie

Le rapport ne contient aucun dialogue, traduction ou chemin personnel complet par défaut.

## Phase 7 — Adaptateur Pokémon Flux expérimental

### Prérequis

Les phases 1 à 6 doivent être stabilisées avant d'autoriser l'écriture Flux.

### Objectifs

- détecter les signatures structurelles Flux connues ;
- reconnaître uniquement les versions explicitement supportées ;
- réutiliser l'expérience du patcher Flux v3.6 ;
- extraire les occurrences vérifiables via un chemin séparé et déterministe ;
- importer puis reconstruire uniquement après validation indépendante de ces deux étapes ;
- relire l'archive produite ;
- bloquer toute version inconnue en écriture.

### Interface

```text
Profil détecté : Pokémon Flux
Adaptateur : expérimental
Version reconnue : oui/non
```

Les fonctions PBS/RXDATA classiques doivent être masquées dans ce mode.

### Condition de sortie

Une fixture artificielle de format Flux passe les tests. Un vrai jeu n'est utilisé que pour une validation locale privée, jamais ajouté au dépôt.

### État progressif actuel

- détection, analyse et extraction synthétique : en place pour la signature
  2.1.0 explicitement reconnue ;
- validation indépendante du CSV : en place, strictement en lecture seule, avec
  réextraction de contrôle et vérification de l'empreinte du FPK original ;
- import/réinjection : non implémenté ;
- reconstruction FPK : volontairement verrouillée.

La prochaine étape autorisée est un plan d'import testable qui ne produit encore
aucune archive. Les essais de réinjection viendront ensuite uniquement sur une
copie de travail, avec relecture complète, contrôle d'intégrité et rollback. La
capacité `RECONSTRUCT` ne sera ajoutée qu'après réussite de ces portes et d'une
validation locale privée sur une copie, jamais sur l'original.

## Hors périmètre initial

- bot universel jouant automatiquement à tous les fangames ;
- exécution de scripts Ruby inconnus ;
- garantie que toute l'histoire est jouable ;
- prise en charge immédiate de toutes les versions de Flux ;
- réparation automatique de logique de scénario ou de scripts personnalisés ;
- publication de fichiers appartenant aux fangames.
