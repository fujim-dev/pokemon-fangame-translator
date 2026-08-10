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

### Stabilisation acquise

- les sondes sont exécutées dans un processus isolé, avec une limite de
  30 secondes par adaptateur et une limite distincte pour le démarrage ;
- une seule sonde expirée ou défaillante invalide toute la décision : aucun
  autre moteur n'est choisi par défaut et les écritures restent bloquées ;
- le worker et ses descendants sont arrêtés à l'expiration, au changement de
  dossier ou à la fermeture de l'application ;
- le thread Tkinter ne bloque plus pendant les sondes et ignore tout résultat
  appartenant à un diagnostic annulé ou remplacé.
- la famille Essentials est subdivisée en profils de capacité : classique RMXP,
  v21.1 avec jeu en lecture seule, et version modifiée/inconnue ;
- la version v21.1 est confirmée par concordance de marqueurs statiques provenant
  de `Game.ini`, `mkxp.json` et de `Scripts.rxdata` décompressé sans exécution ;
- une version contradictoire, v20, future ou insuffisamment confirmée bloque
  extraction, traduction et reconstruction ;
- le profil v21.1 confirmé peut produire une extraction et un projet CSV, mais
  la reconstruction publique reste volontairement absente ;
- le parcours réel dédié a validé une première portée bornée : 30 402 occurrences
  cohérentes entre extraction et analyse, cycle Studio/reprise, une occurrence de
  `messages_game.dat` reconstruite dans une copie intégralement contrôlée, puis
  démarrage concluant de `Game.exe` ;
- cette validation n'active aucune capacité supplémentaire. Les cartes, événements
  communs, sous-champs `Point`, PBS modernes et formes de banques non couvertes
  exigent encore leurs propres preuves synthétiques et réelles.
- un lot suivant a validé séparément les trois formes de banques observées dans
  `messages_core.dat`/`messages_game.dat`, puis une carte bornée à un dialogue
  101/401 et un choix 102/402. Les deux candidats ont été réextraits à 30 402
  occurrences et comparés intégralement. Une vérification humaine en jeu a
  confirmé l'affichage du dialogue, l'affichage du choix et le fonctionnement
  normal du menu ; `RECONSTRUCT` demeure absent.
- une métadonnée de segmentation immuable distingue désormais les frontières
  101/401 des contrôles `\n` internes. Les tests synthétiques couvrent plusieurs
  continuations, commandes voisines, métadonnées altérées et structures invalides ;
- l'extraction des événements communs réutilise cette preuve. Une porte interne
  synthétique accepte seulement trois dialogues répartis sur deux événements et
  revalide l'objet `RPG::CommonEvent` complet, son index/ID, trigger, switch et
  toutes ses commandes avant de modifier les seuls paramètres textuels 101/401 ;
- le round-trip synthétique conserve exactement les commandes voisines, choix et
  branches, ivars, ordre et paramètres non textuels, puis réextrait les trois
  traductions attendues. Un événement manquant/remplacé, une provenance modifiée,
  des preuves incompatibles, un 401 invalide ou un chevauchement est refusé ;
- ce corpus synthétique n'ajoute aucune capacité publique : sa preuve devait être
  répétée sur une copie de travail avant tout élargissement, et `RECONSTRUCT`
  demeure absent ;
- une portée privée unitaire séparée a ensuite validé une occurrence réelle
  simple, sans assouplir le corpus synthétique : extraction de 30 402 lignes,
  sauvegarde/réouverture transactionnelles du Studio, candidat limité à
  `Data/CommonEvents.rxdata`, comparaison des 7 606 fichiers, réextraction à la
  même identité stable ;
- un premier chemin de candidat trop long a provoqué l'erreur mkxp-z d'absence
  de script alors que `Game.ini` et `Scripts.rxdata` étaient byte-identical. Le
  même candidat sous une racine courte atteint l'écran titre et une destination
  privée dépassant 120 caractères est maintenant refusée avant toute copie ;
- la validation humaine a confirmé l'affichage complet de l'unique dialogue 101
  ciblé lors du déclenchement réel de l'événement commun. Cette preuve ne couvre
  pas les choix communs, les dialogues réels avec continuations 401, `Point` ou
  les PBS modernes, et n'ajoute toujours pas `RECONSTRUCT`.
- une portée privée séparée couvre maintenant exactement une occurrence réelle
  101 + une 401. Les tests synthétiques refusent continuation manquante,
  supplémentaire, mal indentée ou déplacée, paramètre inattendu, preuve altérée
  et provenance différente ;
- le parcours réel a extrait 30 402 lignes, validé la session Studio exclusive et
  sa reprise, puis reconstruit uniquement `Data/CommonEvents.rxdata` parmi 7 606
  fichiers du jeu. La réextraction retrouve l'identité stable et le candidat
  court atteint la fenêtre de titre sans modifier ses fichiers. Une validation
  visuelle humaine a confirmé l'affichage complet de cette unique occurrence
  réelle 101 + exactement une 401. Les CommonEvents avec plusieurs 401, les choix
  communs, `Point`, les PBS modernes et la capacité publique `RECONSTRUCT` restent
  bloqués.
- aucun CommonEvent réel 101 + plusieurs 401 n'existe dans la référence v21.1 :
  cette forme reste couverte synthétiquement et attend un futur corpus réel, sans
  modification artificielle de la référence ;
- une portée privée distincte couvre maintenant exactement un libellé de choix
  commun 102 et son unique branche 402. Les tests refusent branche manquante ou
  supplémentaire, sous-index, ordre, indentation ou paramètre hors texte modifié,
  preuve altérée et provenance différente ;
- le parcours technique réel 102/402 a extrait 30 402 lignes, dont 18 occurrences
  CommonEvents et 4 choix, validé sauvegarde/reprise et exclusivité du Studio, puis
  limité le changement à `Data/CommonEvents.rxdata`. La réextraction conserve la
  même identité stable, la référence et la copie de travail restent inchangées et
  le candidat court atteint l'écran titre sans modifier ses fichiers. Une validation
  visuelle humaine a confirmé l'affichage et la sélection du choix modifié, puis la
  navigation normale jusqu'à la fin de sa branche 402. Cette preuve reste limitée
  à cette occurrence réelle ; les autres formes 102/402, le multi-401 réel, `Point`,
  les PBS modernes et `RECONSTRUCT` public demeurent bloqués.

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
- plan d'import en mémoire : en place et déterministe ;
- réinjection expérimentale interne : validée sur archives synthétiques avec
  relecture complète, refus des changements hors plan, contrôle SHA-256 des
  copies et pannes injectées avant installation et pendant le rollback ;
- validation locale privée : réussie sur une occurrence de référence dans une
  copie complète de Flux v2.1.0, avec 669 membres FPK, 741 fichiers du jeu et
  restauration exacte de l'empreinte originale ;
- import/réinjection dans l'interface : non exposé ;
- reconstruction FPK : volontairement verrouillée.

Les prochaines portes sont la couverture synthétique de toutes les formes
d'occurrences prises en charge, la validation privée d'un corpus représentatif,
l'élargissement de la matrice d'échecs injectés (sauvegarde, validation,
inventaire et signalement d'une copie invalide) et un contrôle manuel du
démarrage de la copie. La capacité `RECONSTRUCT` ne sera ajoutée qu'après ces
preuves ; une validation analytique d'une occurrence ne suffit pas à l'activer.

## Hors périmètre initial

- bot universel jouant automatiquement à tous les fangames ;
- exécution de scripts Ruby inconnus ;
- garantie que toute l'histoire est jouable ;
- prise en charge immédiate de toutes les versions de Flux ;
- réparation automatique de logique de scénario ou de scripts personnalisés ;
- publication de fichiers appartenant aux fangames.
