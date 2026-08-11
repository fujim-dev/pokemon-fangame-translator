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
- une seconde preuve 102/402 cible maintenant un choix réel de sous-index 1 et une
  branche éloignée de la commande 102. La structure synthétique exige également
  la fermeture 404 et refuse mauvais sous-index, branches échangées/manquantes ou
  supplémentaires, ordre ou autre choix modifié, indentation, paramètre hors texte,
  404, preuve et provenance altérés ;
- le round-trip réel correspondant conserve le premier choix et sa branche
  byte-identical, modifie uniquement le choix ciblé et sa branche, retrouve la même
  identité stable et ne change que `Data/CommonEvents.rxdata`. Original et travail
  restent inchangés, le candidat court atteint l'écran titre et `RECONSTRUCT` reste
  absent. Une validation visuelle humaine a confirmé l'affichage du second choix
  modifié, l'exécution de sa branche 402 sans passage par la première branche et
  la fin normale de l'appel. Cette seconde preuve reste strictement limitée à
  cette occurrence et ne valide aucune autre forme 102/402.
- une portée privée séparée lie désormais un `Point.Name` réel à trois champs à
  sa ligne et à son fichier `PBS/town_map.txt` complets. La preuve immuable conserve
  position, sous-index, champs, espaces, virgules, commentaires, BOM, CRLF et
  empreintes, puis refuse toute divergence avant écriture ;
- le round-trip réel correspondant a extrait 30 402 occurrences, dont 27 noms et
  8 descriptions Point, traversé le Studio transactionnel, modifié seulement
  `PBS/town_map.txt` et réextrait la traduction à la même identité. Original et
  travail restent inchangés. Cette preuve ne couvre qu'un nom à trois champs et
  reste fichier/structure : descriptions, formes 4/7/8 champs, compilation PBS,
  observation en jeu et `RECONSTRUCT` public demeurent bloqués.
- l'audit compilé suivant a prouvé que la N-ième ligne Point d'une section PBS
  correspond exactement à `Data/town_map.dat[section].@point[N-1]`. Le Marshal
  contient un Hash de `GameData::TownMap` et chaque Point compilé est un tableau
  typé de huit positions ; les champs PBS absents/vides deviennent `nil` sans
  perdre l'identité section/occurrence ;
- l'extraction lie maintenant les 35 sous-champs réels à une preuve compilée
  immuable et refuse toute divergence de sections, métadonnées, nombre de Point,
  types, valeurs ou alias. La porte privée du nom simple synchronise PBS et
  `Data/town_map.dat`, recontrôle les deux sources après planification et préserve
  le graphe Marshal entier hors chaîne ciblée ;
- le nouveau round-trip réel ne change que ces deux fichiers, réextrait la même
  traduction depuis chacun et laisse original, travail et tous les autres
  fichiers identiques. Le candidat démarre sans erreur immédiate et reste
  réactif. Une validation visuelle humaine a confirmé l'affichage du
  `Point.Name` traduit sur la carte régionale. Cette preuve reste limitée à une
  occurrence simple à trois champs synchronisée entre PBS et donnée compilée ;
  les descriptions, formes 4/7/8 champs, autres PBS modernes et `RECONSTRUCT`
  public restent volontairement bloqués.
- les huit `Point.Description` réels ont ensuite été reliés sans ambiguïté au
  compilé : six occurrences à quatre champs et deux à sept champs. Une porte
  privée distincte n'accepte qu'une occurrence simple à quatre champs et exige
  deux entiers, deux `RubyString` puis quatre `nil` dans le tableau compilé ;
- les tests refusent sous-index/nombre de champs, présence de description,
  nom, coordonnées, paramètres numériques, type/structure Marshal, sources ou
  provenance modifiés. La forme réelle à sept champs reste volontairement hors
  reconstruction ;
- le round-trip réel d'une seule description à quatre champs synchronise les
  deux fichiers TownMap, conserve les 30 402 occurrences, l'original, le travail
  et le graphe Marshal hors cible. Le candidat court démarre sans erreur
  immédiate. Une validation visuelle humaine a confirmé l'affichage de cette
  unique description reconstruite sur la carte régionale ; toutes les autres
  descriptions, les formes 7/8 champs et `RECONSTRUCT` public restent bloqués.
- l'audit statique des six Point à sept champs confirme le schéma `^uusSUUUU` :
  deux coordonnées, un nom, une description optionnelle, la destination
  `[map ID, x, y]`, puis un switch optionnel absent et compilé en `nil`. Deux
  occurrences possèdent une description et quatre conservent `nil` ;
- une portée privée distincte accepte exactement une description à sept champs
  et refuse toute divergence de champ, ordre, valeur numérique, `nil`, type,
  structure, source ou provenance. Son premier round-trip réel ne modifie que
  `PBS/town_map.txt` et `Data/town_map.dat`, retrouve la même identité parmi
  30 402 occurrences et conserve le graphe Marshal hors cible, l'original et la
  copie de travail. Le candidat court démarre sans erreur immédiate. Une validation
  visuelle humaine a confirmé l'affichage de cette unique description reconstruite
  sur la carte régionale ; les cinq autres occurrences à sept champs, les formes
  à huit champs et `RECONSTRUCT` public restent bloqués.
- l'audit statique des deux Point réels à huit champs prouve une forme composée
  de deux coordonnées, un nom, quatre champs optionnels absents et un switch de
  visibilité entier. Cette signification est corroborée par le schéma
  `^uusSUUUU` et les accès `point[7]` de l'interface régionale, sans exécution de
  Ruby ;
- une portée privée distincte accepte exactement un nom portant cette preuve et
  refuse forme, ordre, switch, `nil`, type, structure, source ou provenance
  divergents. Son round-trip réel synchronise les deux fichiers TownMap, conserve
  30 402 occurrences et le graphe Marshal hors cible, laisse l'original et le
  travail inchangés, puis démarre le candidat court sans erreur mkxp-z. Une
  validation visuelle humaine a confirmé l'affichage en jeu de cet unique nom
  reconstruit après activation du switch de visibilité réel ciblé. La seconde
  occurrence réelle à huit champs, tout autre corpus et `RECONSTRUCT` public
  restent bloqués.

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
