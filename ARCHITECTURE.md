# Architecture actuelle et cible

## 1. Vue d'ensemble de la v1.0.2

L'application est une application de bureau Python/Tkinter. La base stable v1.0.2
reste organisée autour de sept modules principaux, auxquels s'ajoute désormais la
première brique de migration v1.1 : le paquet `adapters`.

```text
Pokemon_Fangame_Translator.py
├── adapters/
│   ├── base.py
│   ├── essentials_profiles.py
│   ├── registry.py
│   ├── pokemon_essentials.py
│   ├── pokemon_flux.py
│   └── unknown.py
├── analysis/
│   ├── models.py
│   ├── deep_analyzer.py
│   ├── flux_analyzer.py
│   ├── language_coverage.py
│   └── report_writer.py
├── flux_archive.py
├── flux_import_plan.py
├── flux_import_validator.py
├── flux_reinjection.py
├── project_identity.py
├── rpg_dialogue.py
├── safe_io.py
├── translation_project.py
├── structured_extractor.py
│   └── ruby_marshal_reader.py
├── translation_studio.py
└── reconstruction_studio.py
    └── reconstruction_engine.py
        ├── ruby_marshal_reader.py
        ├── ruby_marshal_writer.py
        └── structured_extractor.py
```

## 2. Modules actuels

### `Pokemon_Fangame_Translator.py`

Responsabilités actuelles :

- fenêtre principale et navigation ;
- sélection du dossier ;
- activation d'un projet persistant ;
- diagnostic piloté par un registre d'adaptateurs ;
- affichage des résultats ;
- déclenchement de l'extraction ;
- ouverture des studios de traduction et de reconstruction ;
- génération de rapports ;
- export d'un diagnostic public.

Points importants :

- `Diagnostic` est une dataclass contenant les résultats de détection.
- `run_diagnostic()` utilise plusieurs indices structurels et bloque les capacités lorsque la détection est incertaine.
- `merge_project_rows()` conserve une traduction précédente seulement si l'identifiant stable et le texte source correspondent encore.
- les projets sont stockés dans `Documents/Pokemon Fangame Translator/Projets`.

Risque architectural restant :

- ce fichier concentre encore l'interface, les rapports et une partie de la logique de projet ;
- l'ajout direct de cas Flux dans `run_diagnostic()` rendrait le module difficile à maintenir.

Le diagnostic ne lance plus les sondes dans le thread Tkinter. Le service
`adapters/probe_isolation.py` exécute chaque lot dans un processus `spawn` et
applique une limite de 30 secondes par adaptateur. Une expiration, une
annulation ou un worker invalide annule le lot entier et retourne
`UnknownAdapter` en lecture seule. Sous Windows, un Job Object
`KILL_ON_JOB_CLOSE` contient le worker et ses descendants ; sous POSIX, le même
rôle est assuré par un groupe de processus. Un changement de dossier ou la
fermeture invalide aussi la génération du diagnostic, afin qu'un résultat tardif
ne puisse jamais être réinjecté dans l'interface.

### `structured_extractor.py`

Responsabilités :

- lecture structurée des cartes RPG Maker XP ;
- extraction des commandes de dialogue et de choix ;
- parcours de banques de messages ;
- extraction de certaines clés PBS ;
- création d'identifiants stables ;
- production des lignes CSV.

La segmentation des dialogues est déléguée à `rpg_dialogue.py`. Chaque séquence
contiguë 101/401 produit une métadonnée immuable sans texte : index, code,
indentation, nombre et empreinte des paramètres, empreinte de la commande et
nombre de contrôles `\n` internes. Cette preuve distingue les contrôles contenus
dans un paramètre des séparateurs historiques du CSV sans exécuter Ruby.

Dépendance principale :

- `ruby_marshal_reader.py`.

Limite actuelle :

- l'extracteur est conçu pour les structures classiques prises en charge ;
- il reste l'implémentation Essentials, appelée derrière `PokemonEssentialsAdapter`.

### `translation_studio.py`

Responsabilités :

- interface de traduction et de relecture ;
- intégration Argos Translate ;
- préservation des commandes ;
- glossaire ;
- mémoire de corrections ;
- doublons ;
- filtres et pagination ;
- acceptation, vérification et blocage des traductions ;
- réparation simple de commandes ;
- sauvegarde du CSV.

Les CSV du studio, le glossaire, la mémoire de corrections, l'état de reprise et
les rapports sont écrits atomiquement. Un glossaire ou une mémoire illisible ou
contradictoire bloque le studio sans être remplacé silencieusement.

Pour Pokémon Essentials, `translation_project.py` ouvre désormais une session
exclusive liée à `projet.json`, au manifeste, au CSV témoin et au rapport
d'extraction. Le Studio compare les champs immuables des occurrences, surveille
les identités de fichiers et publie le CSV, son état de provenance et la reprise
comme un lot avec rollback. Un projet ancien reste consultable en lecture seule,
mais doit être réextrait avant sauvegarde, reprise ou reconstruction.

La réparation et la restauration Essentials utilisent `repair/transactional.py` :
le candidat est construit en mémoire, puis le CSV, l'état révisionné, la reprise
inactive, la sauvegarde exacte et le journal sont publiés dans le même lot. Les
anciennes fonctions d'écriture directe refusent tout CSV voisin d'une identité
Essentials ; elles restent uniquement disponibles pour des CSV autonomes non
rattachés aux nouvelles garanties.

Risques :

- module volumineux ;
- la logique de traduction, de revue et l'interface sont étroitement liées ;
- les règles de validation devraient progressivement être isolées dans des services testables.

### `reconstruction_studio.py`

Responsabilités :

- interface de simulation et de reconstruction ;
- choix du dossier cible ;
- affichage de la progression ;
- lancement de la copie française ;
- création d'un raccourci.

La logique métier réelle est déléguée à `reconstruction_engine.py`.

### `reconstruction_engine.py`

Responsabilités :

- chargement du CSV ;
- création d'un plan de reconstruction ;
- filtrage des lignes éligibles ;
- modification des cartes, banques et PBS ;
- vérifications UTF-8 ;
- écritures atomiques ;
- simulation ;
- copie du jeu ;
- validation après écriture ;
- rapports et empreintes SHA-256.

Forces :

- séparation correcte entre interface et reconstruction ;
- modèle de plan explicite ;
- écritures atomiques ;
- validation après modification ;
- préservation de l'original par copie.
- revalidation de l'adaptateur Essentials au plan puis immédiatement avant la copie ;
- refus des racines redirigées et des noms de sortie réservés déjà présents.
- identité privée reliant le CSV au chemin canonique du jeu et à son adaptateur ;
- empreintes du CSV et de l'identité figées dans le plan puis revérifiées avant copie ;
- copie marquée incomplète si un manifeste, rapport ou guide final ne peut pas être écrit.

Limite actuelle :

- moteur conçu pour les formats classiques ;
- l'accès est verrouillé par les capacités de l'adaptateur, mais la stratégie de reconstruction n'est pas encore entièrement encapsulée dans celui-ci.

### `ruby_marshal_reader.py`

Lecteur partiel du format Ruby Marshal utilisé par RPG Maker XP.

Objets principaux :

- `RubyString`
- `RubyObject`
- `RubyUserDefined`
- `MarshalReader`

### `ruby_marshal_writer.py`

Sérialisation des objets lus par le lecteur Marshal.

La compatibilité lecteur/écrivain est critique. Toute évolution doit être testée avec des fixtures synthétiques, sans fichier propriétaire.

## 3. Flux de données actuel

```text
Dossier du jeu
→ AdapterRegistry.detect()
→ PokemonEssentialsAdapter ou UnknownAdapter
→ capacités autorisées dans l'interface
→ PokemonEssentialsAdapter.extract()
→ extract_structured()
→ CSV de projet
→ TranslationStudio
→ CSV traduit et relu
→ build_plan()
→ simulate_plan()
→ reconstruct_copy()
→ VERSION_FR séparée
→ validation des fichiers modifiés
```

### État de la migration v1.1

Déjà en place :

- contrat commun de détection et de capacités ;
- registre avec seuil de confiance et refus des scores ambigus ;
- détection Essentials fondée sur plusieurs indices réels ;
- refus central d'une racine de fangame redirigée avant l'appel des adaptateurs,
  et déclassement en lecture seule des structures Essentials dont un indice
  critique est un lien ou une jonction ;
- détection incomplète si une sonde d'adaptateur échoue : aucun autre profil
  n'est choisi par défaut et toutes les actions d'écriture restent bloquées ;
- `UnknownAdapter` limité à l'analyse en lecture seule ;
- extraction Essentials déléguée à l'adaptateur ;
- boutons et commandes sensibles bloqués pour les structures inconnues ;
- refus des liens symboliques et jonctions pendant la reconstruction.
- analyse approfondie statique des cartes, pages, événements communs, banques et PBS ;
- estimation reproductible de la couverture française sans conserver les dialogues dans le rapport ;
- rapports TXT, JSON et résumé Discord avec limites explicites.
- empreinte complète avant/après reconstruction pour l'original et la copie ;
- refus d'une copie contenant un fichier manquant, inattendu, vidé ou modifié hors plan ;
- empreintes avant/après des seuls fichiers ciblés dans le manifeste, sans dialogues.
- plan de réparation CSV enregistré avant application, sans dialogues dans le rapport ;
- restauration déterministe des commandes protégées simples avec point de restauration ;
- validation après écriture et rollback exact en cas d'échec ;
- restauration manuelle réversible du dernier projet précédant une réparation.
- détection Flux multi-indices avec empreintes de la version 2.1.0 connue ;
- inventaire et analyse statique du FPK Flux dans un dossier temporaire isolé ;
- extraction Flux déterministe vers le CSV commun pour la signature 2.1.0 exacte ;
- identifiants Flux fondés sur le conteneur, la source, le chemin structurel et l'empreinte brute ;
- corrélation statique Audio/Graphics par inventaire, y compris les littéraux Ruby sans exécution ;
- profil Flux volontairement privé de toute capacité de reconstruction.
- validateur d'import Flux indépendant : nouvelle extraction de contrôle,
  rattachement du projet, comparaison exacte des occurrences et des champs
  structurels, préservation ordonnée des commandes/balises et contrôle de
  l'empreinte du FPK avant/après ;
- capacité `VALIDATE_IMPORT` distincte de `RECONSTRUCT` et accordée uniquement à
  la signature Flux 2.1.0 exacte ; un avertissement d'extraction conserve
  l'import futur bloqué.
- plan d'import Flux déterministe construit uniquement en mémoire, avec fragments
  UTF-8, chemins structurels et empreintes figés ;
- moteur de candidat Flux encore interne : application dans un dossier temporaire,
  relecture Marshal, reconstruction 7z séparée, inventaire identique, empreintes
  inchangées pour tous les membres hors plan et réextraction complète ;
- validation sur copie de travail avec sauvegarde externe, installation atomique
  temporaire, comparaison des 741 fichiers puis rollback exact ; une validation
  privée locale a réussi sur une occurrence de `messages_game.dat`, sans test de
  jouabilité et sans publier de contenu du jeu ;
- copie atomique renforcée : refus d'une source modifiée pendant sa lecture et
  contrôle SHA-256 exact pour la sauvegarde, l'installation candidate et le
  rollback Flux ;
- pannes synthétiques avant installation et pendant le rollback : la première
  laisse la copie intacte, la seconde conserve la sauvegarde externe exacte et
  marque explicitement la copie comme inutilisable ;
- comparaisons de confinement Flux effectuées sur les deux chemins canonisés :
  une racine originale exprimée par un alias Windows ou des segments `..` reste
  protégée contre toute réinjection directe et toute création de candidat ;
- autorisation commune du registre réappliquée aux appels directs d'extraction ;
- écritures atomiques communes avec fichiers temporaires voisins et uniques ;
- sérialisation Ruby Marshal atomique avec relecture du temporaire avant
  remplacement ;
- rollback du CSV si la validation ou la journalisation d'une restauration échoue.
- refus d'une réextraction vide, illisible ou incompatible avant remplacement du CSV ;
- sauvegarde exacte et unique du projet avant chaque réextraction.
- extraction Essentials précédée d'un inventaire canonique de ses marqueurs
  d'identité et de toutes les sources Data/PBS prises en charge ; chaque fichier
  est empreinté pendant une lecture stable, sans lien, jonction ni reparse point ;
- parsing Essentials effectué uniquement depuis un instantané temporaire isolé,
  puis second inventaire de l'original : ajout, retrait, remplacement, changement
  d'octets ou d'identité de fichier invalide toute l'extraction ;
- manifeste privé reliant l'inventaire source, chaque ligne CSV, le rapport et
  l'identité du projet ; la reconstruction refuse un CSV Essentials provenant
  d'un autre inventaire ;
- publication transactionnelle du CSV principal, de sa copie compatible, du
  rapport, du manifeste et de l'identité, avec détection des modifications
  concurrentes et rollback exact des artefacts déjà remplacés.
- session de traduction Essentials exclusive avec verrou interprocessus, contrôle
  périodique du CSV et de ses artefacts, détection des remplacements à octets
  identiques et état de traduction révisionné ; CSV et reprise sont publiés dans
  une transaction commune et la reconstruction exige la même provenance.
- réextraction refusée tant que le projet est ouvert dans le Studio ; une
  publication réussie régénère ensemble l'état de traduction et une reprise
  inactive afin qu'aucun état d'une extraction précédente ne soit réutilisé.
- réparation/restauration Essentials sous le même verrou de session : plan et
  sauvegarde source surveillés par empreinte et identité, candidat calculé en
  mémoire, publication commune du CSV, de l'état, de la reprise, du point de
  restauration et du journal ; un rollback incomplet conserve explicitement les
  fichiers temporaires exacts nécessaires à la récupération.
- profils Essentials désormais séparés : `essentials_legacy_rxmp` conserve le
  chemin classique déjà validé, `essentials_v21_1_readonly` autorise l'analyse,
  l'extraction et le projet CSV sans écriture dans le jeu, et
  `essentials_modified_or_unknown` reste limité à l'analyse statique ;
- inspection de version v21.1 isolée dans `adapters/essentials_profiles.py` :
  `Game.ini`, `mkxp.json` et la constante compressée de `Scripts.rxdata` sont
  comparés sans exécuter Ruby. Les tailles Marshal/zlib sont bornées et toute
  contradiction déclasse le profil en lecture seule ;
- les événements communs Essentials font partie de l'inventaire et du CSV avec
  des identifiants d'occurrence précis. Les métriques d'analyse profonde utilisent
  les mêmes catégories extractibles (cartes, événements communs, banques et PBS) ;
- les champs PBS modernes confirmés sont lus avec conservation de la casse, des
  commandes, de l'encodage, du BOM et des fins de ligne. Les sous-champs `Point`
  sont exposés pour traduction ; leur reconstruction publique reste explicitement
  bloquée ;
- le profil Essentials et la méthode de version sont liés aux lignes CSV et au
  manifeste privé. Une identité de projet qui annonce un autre profil que son
  manifeste est refusée.
- une porte interne de validation v21.1 permet désormais un candidat strictement
  borné à une seule occurrence acceptée de `Data/messages_game.dat`. Le plan est
  lié au profil, à la provenance et à l'empreinte de la banque ; le fichier
  reconstruit doit être identique, octet par octet, au candidat calculé en mémoire,
  et aucun autre fichier du jeu ne peut changer ;
- cette porte a réussi un aller-retour privé sur la référence standard v21.1 :
  30 402 occurrences extraites, reprise du Studio vérifiée, banque réextraite à
  l'occurrence exacte, copie intégralement comparée et démarrage de `Game.exe`
  conclu avec fermeture propre. Aucun contenu de cette référence n'est conservé
  dans le dépôt.
- deux portées internes supplémentaires couvrent maintenant, sans exposer
  `RECONSTRUCT`, les trois formes de banques réellement observées (core directe,
  game directe et game imbriquée), puis exactement un dialogue 101/401 et un
  choix 102 avec sa branche 402 de la même page de carte ;
- l'extraction enregistre l'index exact de la branche 402 et son paramètre de
  libellé. Une branche absente, dupliquée ou incohérente reste extractible en
  lecture seule mais bloque le candidat. Les classes RPG, identifiants de carte,
  pages, commandes, indentations et paramètres hors texte sont revérifiés avant
  la mutation ;
- un second parcours privé réel a validé trois occurrences de banques dans deux
  fichiers et une carte contenant un dialogue 101/401 ainsi qu'un choix 102/402.
  Chaque candidat a conservé les 30 402 occurrences, modifié uniquement les
  fichiers planifiés et laissé la référence comme la copie de travail inchangées ;
- une validation visuelle humaine dans le moteur v21.1 a confirmé que le choix
  102/402 reconstruit s'affiche et que le menu reste fonctionnel, ainsi que
  l'affichage du dialogue 101/401 reconstruit. Cette preuve reste strictement
  bornée à ces occurrences et n'accorde toujours pas `RECONSTRUCT` au profil ;
- la segmentation 101/401 est désormais explicite et déterministe. Un round-trip
  synthétique couvre plusieurs continuations, des contrôles `\n` internes et des
  commandes voisines sans les modifier. Une continuation orpheline, mal indentée,
  sans paramètre standard, ou une preuve absente/altérée bloque l'opération ;
- les événements communs utilisent le même segmentateur pendant l'extraction et
  exposent la même preuve immuable. Une portée interne synthétique, toujours
  inaccessible à l'interface, exige exactement trois dialogues répartis sur deux
  événements de `Data/CommonEvents.rxdata`. Elle lie chaque occurrence à l'index
  du tableau Marshal, à `@id`, `@trigger`, `@switch_id` et à l'empreinte complète
  de l'objet `RPG::CommonEvent` ;
- avant la première mutation, le flux complet de commandes de chaque événement
  ciblé est revalidé par le segmentateur commun. L'événement, ses commandes non
  textuelles, choix/branches, indentations, paramètres supplémentaires, ivars et
  ordre doivent rester identiques. Un événement manquant/remplacé, un 401
  orphelin ou mal indenté, des preuves incompatibles ou un chevauchement bloque
  le candidat. Le fichier relu doit être identique au payload complet calculé en
  mémoire et la réextraction doit retrouver les trois traductions exactes ;
- une seconde portée privée, plus étroite, accepte exactement un dialogue commun
  composé d'une seule commande 101. Elle conserve la portée synthétique intacte
  et sert uniquement au round-trip réel d'une occurrence dont la provenance,
  l'événement complet et la segmentation ont été confirmés ;
- cette portée unitaire a réussi sur une copie séparée de la référence v21.1 :
  30 402 occurrences extraites, cycle Studio transactionnel vérifié, seul
  `Data/CommonEvents.rxdata` modifié parmi 7 606 fichiers du jeu, réextraction à
  la même identité stable. Le premier essai de lancement a révélé une limite de
  chemin mkxp-z : un candidat sous une racine de 145 caractères affiche à tort
  l'absence de script malgré un `Game.ini` et un `Scripts.rxdata` intacts. Une
  nouvelle copie sous une racine courte atteint réellement l'écran titre ; les
  portées privées refusent désormais par prudence toute racine dépassant 120
  caractères avant la copie. Une validation humaine a ensuite confirmé dans le
  jeu l'affichage complet de l'unique dialogue 101 ciblé. Cette preuve ne couvre
  aucun 401 réel ni choix commun, et aucun contenu du jeu n'est conservé dans le
  dépôt ;
- une troisième portée privée, distincte des deux précédentes, exige exactement
  une séquence commune 101 suivie d'une seule 401 de même indentation. Elle lie
  l'unique ligne acceptée aux empreintes des deux commandes/paramètres, au nombre
  de contrôles `\n` internes et à l'événement complet. Les tests refusent une 401
  manquante, supplémentaire, déplacée ou mal indentée, un paramètre inattendu,
  ainsi qu'une preuve de segmentation ou une provenance altérée ;
- le round-trip réel correspondant a conservé l'identité stable parmi 30 402
  occurrences et limité les changements du jeu à `Data/CommonEvents.rxdata` sur
  7 606 fichiers. Le Studio a refusé une seconde session, puis a sauvegardé et
  rouvert le projet avec une reprise cohérente. Le candidat court atteint la
  fenêtre de titre v21.1 et reste byte-identical après ce simple démarrage. Une
  validation visuelle humaine a confirmé l'affichage complet de cette unique
  occurrence réelle 101 + exactement une 401. Aucun CommonEvent avec plusieurs
  401, choix commun, `Point` ou PBS moderne n'est déduit de cette preuve, et
  `RECONSTRUCT` public reste absent ;
- la référence v21.1 ne contient aucune occurrence commune réelle 101 suivie de
  plusieurs 401. Cette forme demeure démontrée uniquement par fixtures synthétiques
  et n'a pas été fabriquée dans le projet de référence ;
- une quatrième portée privée réutilise la validation 102/402 des cartes dans le
  conteneur `RPG::CommonEvent`. Elle exige exactement un libellé accepté, la preuve
  complète de l'événement, le sous-index 102 et l'unique branche 402 correspondante,
  puis modifie seulement ces deux chaînes. Une branche absente/supplémentaire, un
  ordre, une indentation, un paramètre hors texte, une preuve ou une provenance
  modifiés sont refusés ;
- le round-trip réel 102/402 a extrait 30 402 occurrences, traversé la session
  Studio exclusive et sa reprise, limité le changement du jeu à
  `Data/CommonEvents.rxdata`, puis retrouvé la même identité stable. Le candidat
  court atteint l'écran titre et reste inchangé après fermeture. Une validation
  visuelle humaine a confirmé l'affichage du libellé modifié, sa sélection et la
  navigation normale jusqu'à la fin de l'unique branche 402 ciblée. Cette porte
  n'accorde pas `RECONSTRUCT` ;
- la même porte privée a ensuite été éprouvée sur une seconde forme réelle : un
  choix de sous-index 1 dont la branche 402 est éloignée de la commande 102. Le
  validateur exige désormais aussi une fermeture 404 de même indentation. Les
  tests refusent sous-index erroné, branches échangées/absentes/supplémentaires,
  ordre ou autre choix modifié, indentation, paramètre hors texte, 404, preuve ou
  provenance altérés ;
- le second round-trip réel conserve le premier choix et sa branche byte-identical,
  ne remplace que le libellé ciblé et sa branche, retrouve la même identité stable
  et limite encore le changement du jeu à `Data/CommonEvents.rxdata`. Le candidat
  court atteint l'écran titre sans modifier ses fichiers. Une validation humaine
  a confirmé l'affichage du second choix modifié, l'exécution de sa branche 402
  sans passage par la première branche et la fin normale de l'appel ;
  `RECONSTRUCT` public demeure absent ;
- une cinquième portée privée couvre exactement un `Point.Name` réel à trois
  sous-champs dans `PBS/town_map.txt`. L'extraction attache une preuve sans texte
  à la ligne et au fichier complets : position, section/occurrence, champs,
  espaces, séparateurs, BOM, CRLF et empreintes des parties ciblées/non ciblées.
  Cette preuve devient une métadonnée immuable du projet Studio ;
- les tests refusent sous-champ manquant/supplémentaire, ordre, séparateur, valeur
  non textuelle, commentaire, espace, BOM, fins de ligne, source, preuve ou
  provenance modifiés. Le round-trip réel a extrait 30 402 occurrences, dont 35
  sous-champs Point, traversé le Studio et sa reprise, modifié uniquement
  `PBS/town_map.txt`, puis réextrait le même identifiant et le même sous-index. La
  référence et la copie de travail sont restées inchangées. La preuve reste
  structurelle : elle ne compile pas les PBS et n'accorde pas `RECONSTRUCT` ;
- `essentials_town_map.py` corrèle maintenant sans Ruby chaque Point PBS à
  `Hash[section].@point[occurrence-1]` dans le Marshal 4.8 compilé. Il accepte
  uniquement des objets `GameData::TownMap` aux six attributs attendus et des
  tableaux Point de huit positions typées. L'extraction exige aussi l'égalité
  globale des sections, identifiants, noms, fichiers graphiques et nombres de
  Point, ainsi qu'un aller-retour Marshal byte-identical ;
- la preuve Point immuable contient désormais le chemin compilé, l'empreinte de
  `Data/town_map.dat`, les types, l'ordre, le graphe complet et le graphe masqué
  hors chaîne ciblée. Une chaîne partagée ou une divergence PBS/compilé bloque
  l'extraction au lieu de produire un CSV apparemment cohérent ;
- la portée privée du `Point.Name` à trois champs publie maintenant de manière
  coordonnée `PBS/town_map.txt` et `Data/town_map.dat`. Les deux sources sont
  recontrôlées après simulation et les deux candidats sont relus avant succès.
  Le round-trip réel ne modifie que ces deux fichiers, conserve le graphe Marshal
  hors cible et réextrait la même traduction depuis PBS et compilé. Le candidat
  démarre et reste réactif. Une validation visuelle humaine a confirmé
  l'affichage du `Point.Name` traduit sur la carte régionale. Cette preuve ne
  s'étend ni aux descriptions, ni aux formes 4/7/8 champs, ni à la capacité
  publique `RECONSTRUCT` ;
- une sixième portée privée traite séparément exactement une
  `Point.Description` à quatre champs. Elle exige un tableau compilé de huit
  positions typées (`Integer`, `Integer`, deux `RubyString`, puis quatre `nil`)
  et conserve le nom, les coordonnées ainsi que tous les nœuds hors cible ;
- les huit descriptions réelles ont une correspondance PBS/Marshal bijective :
  six formes à quatre champs et deux à sept champs. Seule une occurrence de la
  première forme est admise par la nouvelle porte. Son round-trip réel synchronise
  uniquement `PBS/town_map.txt` et `Data/town_map.dat`, conserve les 30 402
  occurrences et le graphe Marshal hors cible, puis démarre le candidat sous un
  chemin court. Une validation visuelle humaine a confirmé l'affichage de cette
  unique description à quatre champs sur la carte régionale. Aucune autre
  description, forme 7/8 champs ni capacité publique n'en est déduite ;
- les six Point réels à sept champs suivent le schéma statique `^uusSUUUU` :
  coordonnées de carte régionale, nom, description optionnelle, destination
  `[map ID, x, y]`, puis un switch optionnel absent et compilé en `nil`. Deux
  occurrences portent une description et quatre un `nil` à sa place ;
- une septième portée privée cible une seule description à sept champs et exige
  exactement deux `Integer`, deux `RubyString`, trois `Integer`, puis `nil`. Son
  round-trip réel synchronise uniquement les deux fichiers TownMap, conserve les
  30 402 occurrences, l'original, le travail et le graphe Marshal hors cible,
  puis démarre le candidat sous un chemin court. Une validation visuelle humaine
  a confirmé l'affichage de cette unique description reconstruite sur la carte
  régionale. Aucune des cinq autres occurrences à sept champs n'en est déduite ;
- les deux Point réels à huit champs ont une description et une destination
  absentes, puis un entier final. Le schéma `^uusSUUUU` et les accès statiques
  `point[7]` de l'interface régionale démontrent que cet entier est le switch de
  visibilité du Point ; aucun script Ruby n'a été exécuté pour cette preuve ;
- une huitième portée privée exige exactement deux `Integer`, une `RubyString`,
  quatre `nil`, puis un `Integer`, et ne cible que le nom d'une occurrence. Son
  round-trip réel synchronise uniquement les deux fichiers TownMap, conserve les
  30 402 occurrences, l'original, le travail, le switch et le graphe Marshal hors
  cible, puis démarre le candidat sous un chemin court sans erreur mkxp-z. Une
  validation visuelle humaine a confirmé l'affichage en jeu de cet unique nom
  reconstruit après activation du switch de visibilité réel ciblé. La seconde
  occurrence à huit champs et tout autre corpus ne sont pas généralisés ;
- la seconde occurrence Point à huit champs possède exactement le même schéma
  compilé et les mêmes quatre positions `nil` que la première ; seules ses
  valeurs métier diffèrent. Elle n'apporte donc aucune couverture structurelle
  supplémentaire et ne justifie pas une nouvelle porte privée ;
- `essentials_phone.py` corrèle désormais, sans exécuter Ruby, chaque message
  de `PBS/phone.txt` avec son tableau dans un objet
  `GameData::PhoneMessage` de `Data/phone.dat`, puis avec sa clé et sa valeur
  dans `PHONE_MESSAGES` à l'index 22 de `Data/messages_game.dat`. Les preuves
  immuables conservent empreintes, chemins, types, ordre, cardinalité,
  références et graphes Marshal hors cible ;
- une neuvième portée privée reste bornée à un seul message `End`. Elle calcule
  les trois fichiers en mémoire et les publie par `atomic_write_bundle`, avec
  contrôle concurrent et rollback des fichiers déjà remplacés. Le round-trip
  réel a conservé 30 402 occurrences, n'a modifié que les trois fichiers
  annoncés parmi 7 606 fichiers et a réextrait la traduction dans les trois
  représentations. La référence et le travail sont restés inchangés. Une
  validation visuelle humaine a confirmé le marqueur complet et la fin normale
  de l'appel. Cette preuve ne s'étend à aucun autre message ou contact et ne
  change aucune capacité publique ;
- `essentials_trainer.py` corrèle désormais, sans exécuter Ruby, les `LoseText`
  visibles de `PBS/trainers.txt` avec les objets `GameData::Trainer` de
  `Data/trainers.dat`, puis avec la banque `TRAINER_SPEECHES_LOSE` à l'index 23
  de `Data/messages_game.dat`. Les preuves immuables lient l'identité
  type/nom/version, les ivars, les chemins, types, références, cardinalités et
  graphes Marshal hors cible. La valeur spéciale `...` est conservée mais reste
  hors extraction traduisible ; `MegaMessage`, confirmé comme sélecteur entier
  par le schéma statique, n'est plus présenté comme texte ;
- une dixième portée privée accepte exactement un `LoseText` réel, unique et
  non partagé. Elle recalcule les trois représentations en mémoire et les publie
  transactionnellement avec rollback. La preuve réelle a recensé 20 occurrences
  structurelles, 19 visibles et 7 uniques admissibles, conservé les 30 402
  occurrences extraites, laissé la référence et le travail inchangés, puis
  réextrait la traduction sous la même identité stable dans les trois fichiers.
  Une validation visuelle humaine a confirmé le marqueur complet après un combat
  réel, la fin normale du dialogue et le retour au jeu. Cette preuve reste
  limitée à cette seule occurrence et ne change aucune capacité publique ;
- `essentials_ability.py` corrèle désormais, sans exécuter Ruby, chaque
  `Description` de `PBS/abilities.txt` avec son objet `GameData::Ability` dans
  `Data/abilities.dat`, puis avec la banque `ABILITY_DESCRIPTIONS` à l'index 11
  de `Data/messages_core.dat`. Les preuves immuables couvrent identifiant, nom,
  flags, suffixe PBS, ivars, ordre, types, chemins, références et graphes Marshal
  hors cible. L'inventaire réel compte 267 occurrences, dont 240 uniques
  admissibles et 27 partagées volontairement bloquées ;
- une onzième portée privée accepte exactement une description réelle unique,
  recalcule les trois représentations en mémoire et les publie
  transactionnellement avec rollback. Le round-trip réel a conservé les
  30 402 occurrences, laissé la référence et le travail inchangés, limité les
  changements aux trois fichiers prévus et réextrait la chaîne complète sous la
  même identité stable. La validation humaine a confirmé la bonne fiche, la
  description source et le préfixe visible `[TEST PFT ...`, ainsi que la fermeture
  normale de la fiche. La fin du marqueur est tronquée par l'interface et n'est
  donc pas considérée comme observée visuellement. Cette preuve reste limitée à
  cette occurrence et ne change aucune capacité publique ;
- `essentials_species.py` corrèle désormais, sans exécuter Ruby, les 898 entrées
  `Pokedex` de base de `PBS/pokemon.txt` avec les 339 formes déclarées dans
  `PBS/pokemon_forms.txt`, les 1 237 objets `GameData::Species` de
  `Data/species.dat`, puis la banque `POKEDEX_ENTRIES` à l'index 3 de
  `Data/messages_core.dat`. L'ordre composé espèce/forme, les 136 redéfinitions,
  les 203 héritages réels et les références Marshal font partie de la preuve ;
- une douzième portée privée accepte exactement une entrée Pokédex de base,
  unique et non héritée. Les deux PBS sont revalidés, mais seuls
  `PBS/pokemon.txt`, `Data/species.dat` et `Data/messages_core.dat` peuvent être
  publiés transactionnellement ; `pokemon_forms.txt` reste byte-identical. Le
  round-trip réel a conservé les 30 402 occurrences, laissé la référence et la
  copie de travail inchangées, puis réextrait le même texte dans les trois
  représentations. Une validation humaine a confirmé l'affichage intégral du
  marqueur `[TEST PFT v21.1 POKEDEX]`, la continuation immédiate du texte normal
  de l'entrée, puis la fermeture du Pokédex et le retour au jeu sans erreur.
  Cette preuve reste limitée à cette occurrence : les entrées partagées ou
  héritées, les catégories, noms/formes et autres champs d'espèce ne sont pas
  généralisés et aucune capacité publique ne change ;

- `essentials_map_metadata.py` corrèle désormais, sans exécuter Ruby, les 69
  champs `Name` de `PBS/map_metadata.txt` avec les clés numériques et objets
  `GameData::MapMetadata` de `Data/map_metadata.dat`, puis avec la banque
  `MAP_NAMES` située à l'index 21 de `Data/messages_game.dat`. Les preuves
  immuables couvrent l'identifiant numérique, l'ordre des sections, les 25
  ivars, les types, chemins, empreintes, cardinalités et graphes Marshal hors
  cible ; un nom partagé par plusieurs cartes ou une collision de clé reste
  volontairement bloqué ;
- une treizième portée privée accepte exactement un nom de carte réel et unique.
  Elle recalcule les trois représentations en mémoire et les publie par un lot
  transactionnel avec rollback. La clé Ruby réelle ciblée étant également
  référencée dans une autre banque, la mutation remplace uniquement la paire de
  `MAP_NAMES` et prouve que cette banque alias ainsi que tout le graphe extérieur
  restent inchangés. Le round-trip réel a conservé les 30 402 occurrences,
  limité les modifications à `PBS/map_metadata.txt`, `Data/map_metadata.dat` et
  `Data/messages_game.dat`, laissé la référence et la copie de travail
  inchangées, puis réextrait le même nom sous la même identité stable. Une
  validation humaine a confirmé l'affichage clair et intégral du marqueur dans
  le bandeau de nom de lieu lors de l'entrée sur la carte ciblée. Cette preuve ne
  couvre aucun nom partagé, aucun autre champ `MapMetadata`, aucun autre corpus
  et ne change aucune capacité publique ;

- `essentials_move.py` corrèle désormais, sans exécuter Ruby, les 740 `Name` et
  740 `Description` de `PBS/moves.txt` avec les 740 objets `GameData::Move` de
  `Data/moves.dat`, puis avec `MOVE_NAMES` et `MOVE_DESCRIPTIONS` aux index 5 et
  6 de `Data/messages_core.dat`. Le schéma compilé exact comporte 14 ivars ;
  `Category` est l'enum technique Physical/Special/Status, compilé en 0/1/2,
  et n'est jamais exposé comme texte traduisible. Ce filtrage retire les 740
  fausses occurrences `Category` de l'extraction réelle, qui passe de 30 402 à
  29 662 occurrences utiles ;
- une quatorzième portée privée accepte exactement une `Move.Description`
  unique, sans alias de source ni de graphe Marshal. Les preuves lient les trois
  empreintes, l'identifiant, l'ordre PBS, tous les champs techniques, la
  catégorie numérique, les 14 ivars, les chemins de banque et les références.
  La publication des trois fichiers est transactionnelle avec rollback. Le
  round-trip réel sur `TACKLE` a modifié uniquement `PBS/moves.txt`,
  `Data/moves.dat` et `Data/messages_core.dat`, réextrait exactement le même
  marqueur sous la même identité stable, laissé la référence et la copie de
  travail inchangées sur 7 606 fichiers, puis atteint l'écran titre sans erreur
  ni écriture au lancement. Parmi les 740 descriptions, 693 satisfont les
  critères stricts de cible unitaire ; les occurrences partagées ou aliasées
  restent bloquées. Une validation visuelle humaine a confirmé, sur la fiche
  Moves de Bulbasaur, que `TACKLE` conserve son nom et que le marqueur complet
  de sa description reconstruite s'affiche sur deux lignes ; l'écran et le
  retour au jeu restent fonctionnels. Cette preuve porte uniquement sur cette
  occurrence réelle de `Move.Description` : elle ne couvre aucune autre Move,
  aucun `Move.Name` ni aucune autre structure. `Category` reste une donnée
  technique non traduisible et aucune capacité publique ne change ;

- `essentials_item.py` corrèle désormais, sans exécuter Ruby, les cinq familles
  textuelles observées dans `PBS/items.txt` (`Name`, `NamePlural`, les deux noms
  de portion optionnels et `Description`) avec les objets `GameData::Item` de
  `Data/items.dat` et les banques dédiées de `Data/messages_core.dat`. Le schéma
  compilé réel comporte 17 ivars. Les poches, prix, usages terrain/combat,
  flags, booléens, identifiants de Move et suffixes restent des données
  techniques, même lorsque leur représentation ressemble à du texte ;
- une quinzième portée privée accepte exactement une `Item.Description` unique,
  non partagée et reliée sans ambiguïté aux trois représentations. Elle valide
  l'ordre PBS, BOM/CRLF, les 17 ivars, tous les champs techniques, les chemins,
  types, cardinalités et graphes Marshal hors cible, puis publie les trois
  fichiers dans une transaction avec rollback. Sur la référence réelle, 2 274
  preuves triples sont démontrées pour 2 275 occurrences Item : une description
  dont la clé de banque est ancienne reste volontairement sans preuve. Parmi
  les 692 descriptions corrélées, 632 satisfont les critères unitaires stricts.
  Le premier round-trip réel a modifié seulement `PBS/items.txt`,
  `Data/items.dat` et `Data/messages_core.dat`, réextrait la même traduction
  dans les trois représentations, laissé référence et copie de travail
  byte-identical et démarré sans erreur immédiate. Une validation humaine a
  ensuite confirmé, pour une occurrence réelle de `Potion.Description`, que le
  nom reste `Potion`, que le marqueur complet apparaît au début de la
  description et que le début du texte normal s'affiche immédiatement après.
  La fin éventuellement hors zone visible n'est pas revendiquée. L'interface
  et le retour au jeu restent fonctionnels. Cette preuve ne couvre aucun
  `Item.Name`, pluriel, nom de portion, autre Item ou champ technique, et aucune
  capacité publique ne change ;

- `essentials_species_category.py` ajoute une corrélation distincte, dépendante
  du schéma, entre les 898 `Category` de base de `PBS/pokemon.txt`, les objets
  `GameData::Species` de `Data/species.dat` et `SPECIES_CATEGORIES` à l'index 2
  de `Data/messages_core.dat`. Ce nom de clé n'accorde aucun droit générique :
  `PBS/moves.txt/Category` reste l'enum technique Physical/Special/Status 0/1/2,
  absent du CSV traduisible et explicitement refusé par la reconstruction ;
- une seizième portée privée accepte exactement une catégorie d'espèce de base,
  unique, non partagée et non héritée. Les deux PBS Species sont surveillés,
  mais seuls `PBS/pokemon.txt`, `Data/species.dat` et
  `Data/messages_core.dat` peuvent être publiés transactionnellement avec
  rollback. Le round-trip réel a conservé 29 662 occurrences, dont 898
  catégories de base et 374 cibles unitaires admissibles, laissé la référence
  et le travail inchangés puis réextrait la chaîne complète sous la même
  identité. La validation humaine sur une occurrence réelle de Squirtle a
  confirmé le nom et le texte Pokédex inchangés, le remplacement de la catégorie,
  le préfixe visible `[TEST PFT v21.1 SPECIES C...` et le retour normal au jeu.
  La fin du marqueur, tronquée par l'interface, n'est pas revendiquée comme
  observée. Cette preuve ne couvre aucun `Species.Name`, `Species.Pokedex`,
  `FormName`, autre espèce ou forme, et aucune capacité publique ne change ;

Encore partiel ou pas encore en place :

- branches dynamiques et références statiques avancées de l'analyse profonde ;
- autres réparations déterministes au-delà des commandes protégées simples ;
- exposition de l'import/réinjection Flux dans l'adaptateur et l'interface ; les
  composants internes existent mais restent volontairement inaccessibles à
  l'utilisateur ;
- validation d'un corpus traduit représentatif de toutes les sources Flux,
  tests de démarrage/jouabilité et multiplication des scénarios de rollback ;
- encapsulation complète de la stratégie de reconstruction dans chaque adaptateur.
- limite de temps des analyses statiques approfondies autres que les sondes :
  leur durée n'est pas encore bornée par le service d'isolation des adaptateurs.
- élargissement réel des événements communs à un corpus multi-401 et 102/402 plus
  représentatif au-delà de l'unique choix commun désormais validé, autres formes
  `Point` (autres descriptions, autres cas à sept champs et autres formes/cas à
  huit champs), autres formes de messages téléphone,
  PBS modernes et corpus
  réel encore plus large de cartes/banques. La capacité publique `RECONSTRUCT` reste absente
  malgré les preuves techniques et les validations visuelles déjà conclues.

## 4. Problèmes à éviter dans la v1.1

- ajouter des `if flux` dispersés dans l'interface et les moteurs ;
- détecter un jeu uniquement grâce à son nom ;
- exécuter les scripts Ruby du jeu ;
- permettre à l'utilisateur de choisir manuellement un adaptateur incompatible ;
- annoncer une compatibilité complète sans connaître les zones non analysées ;
- écrire dans le dossier original ;
- introduire des tests dépendant d'un fangame réel.

## 5. Architecture cible recommandée

### 5.1 Paquet `adapters`

Structure suggérée :

```text
adapters/
├── base.py
├── essentials_profiles.py
├── registry.py
├── pokemon_essentials.py
├── pokemon_flux.py
└── unknown.py
```

#### `base.py`

Définir les contrats :

```python
class GameAdapter(Protocol):
    adapter_id: str
    display_name: str

    def probe(self, root: Path) -> DetectionResult: ...
    def analyze(self, root: Path, options: AnalysisOptions) -> AnalysisReport: ...
    def extract(self, root: Path, project_dir: Path) -> ExtractionResult: ...
    def build_reconstruction_plan(self, root: Path, csv_path: Path) -> ReconstructionPlan: ...
    def reconstruct(self, plan, target_root: Path) -> ReconstructionResult: ...
```

Les signatures finales peuvent être adaptées, mais les responsabilités doivent rester séparées.

#### `registry.py`

- exécute les sondes de tous les adaptateurs en lecture seule ;
- compare les niveaux de confiance ;
- refuse un choix ambigu ;
- retourne une décision explicite et les preuves utilisées.

#### `pokemon_essentials.py`

Encapsule progressivement les fonctions existantes :

- diagnostic Essentials ;
- `extract_structured()` ;
- reconstruction actuelle.

La première étape est une enveloppe sans réécriture fonctionnelle.

La détection Essentials ne vaut plus autorisation générale. Le résultat distingue
la famille, la version déclarée, la méthode de détection et le profil structurel,
puis expose séparément les compatibilités d'analyse, d'extraction, de projet de
traduction, d'écriture dans le jeu et de reconstruction validée. Pour v21.1,
`RECONSTRUCT` reste absent malgré un premier aller-retour réel concluant limité à
une banque de messages. Des preuves internes supplémentaires couvrent désormais
les trois formes de banques observées et une page de carte bornée à un dialogue
101/401 et un choix 102/402. Une preuve de segmentation permet aussi de traiter
synthétiquement les contrôles `\n` internes sans confondre une commande avec une
frontière 101/401. Une portée synthétique supplémentaire couvre trois dialogues
répartis sur deux événements communs et préserve le reste de leur structure
Marshal complète. Deux portées unitaires distinctes possèdent désormais chacune
une preuve réelle et une validation visuelle humaine : la première est limitée à
un dialogue commun simple composé d'une commande 101 ; la seconde, à une commande
101 suivie d'exactement une continuation 401. Ces portes ne couvrent ni les
CommonEvents avec plusieurs 401. Une quatrième portée privée possède une preuve
réelle et une validation visuelle humaine limitées à un premier choix commun
102/402 et à sa branche correspondante. La même porte possède une seconde preuve
réelle et une validation visuelle humaine sur un choix de sous-index non nul et
sa branche éloignée. Ces portes ne couvrent ni les autres formes de choix communs,
ni les sous-champs `Point`, ni l'ensemble des PBS modernes ; un corpus réel
représentatif plus large reste nécessaire avant toute autorisation générale.

#### `pokemon_flux.py`

Adaptateur expérimental séparé :

- reconnaissance des signatures Flux connues ;
- validation des chemins et de l'inventaire interne du FPK ;
- analyse statique dans un dossier temporaire, sans exécuter Ruby ;
- extraction des textes vérifiables vers le CSV commun ;
- conservation des octets, commandes, balises et chemins structurels nécessaires à la fidélité ;
- validation indépendante du CSV contre une réextraction fraîche, sans créer de
  copie de travail ni modifier une archive ;
- reconstruction FPK sécurisée à venir ;
- blocage des versions inconnues.

Il ne dépend pas d'un nom de dossier. Tant que la reconstruction n'a pas passé
les tests privés sur une copie locale propre, la version 2.1.0 exacte est limitée
à `ANALYZE`, `DEEP_ANALYZE`, `EXTRACT`, `TRANSLATE` et `VALIDATE_IMPORT`. Cette
dernière capacité confirme seulement qu'un CSV serait admissible à l'étape
suivante : elle n'importe et ne réinjecte rien. La capacité `RECONSTRUCT` reste
absente.

`flux_import_plan.py` et `flux_reinjection.py` sont des portes internes de
validation. Leur présence ne constitue pas une compatibilité de reconstruction :
aucune méthode de reconstruction n'est exposée par `PokemonFluxAdapter`, aucun
bouton n'est activé et le candidat n'est jamais installé dans le jeu original.

#### `unknown.py`

- rapport de structure ;
- inventaire des fichiers ;
- aucune extraction destructive ;
- aucune traduction ;
- aucune reconstruction.

### 5.2 Paquet `analysis`

Structure suggérée :

```text
analysis/
├── models.py
├── deep_analyzer.py
├── integrity.py
├── language_coverage.py
├── event_graph.py
└── report_writer.py
```

Responsabilités :

- modèles de rapport communs ;
- analyse statique profonde ;
- intégrité ;
- couverture de traduction ;
- graphe des cartes et événements ;
- génération de rapports texte, JSON et éventuellement HTML.

### 5.3 Paquet `repair`

Structure suggérée :

```text
repair/
├── models.py
├── planner.py
├── engine.py
├── transactional.py
├── project_guard.py
├── safe_fixes.py
└── rollback.py
```

Principe :

```text
détection
→ proposition d'un plan
→ sauvegarde
→ application des corrections sûres
→ validation
→ validation réussie ou rollback
```

Aucune réparation ambiguë ne doit être appliquée automatiquement.

### 5.4 Paquet `tests`

```text
tests/
├── fixtures/
├── test_adapter_detection.py
├── test_essentials_adapter.py
├── test_unknown_adapter.py
├── test_deep_analysis.py
├── test_language_coverage.py
├── test_safe_repairs.py
├── test_reconstruction_safety.py
└── test_marshal_roundtrip.py
```

Les fixtures doivent être artificielles et minimales.

## 6. Modèles de données recommandés

### `DetectionEvidence`

- identifiant de l'indice ;
- chemin relatif ;
- valeur observée ;
- poids ;
- explication publique.

### `DetectionResult`

- `adapter_id` ;
- `confidence` ;
- `recognized_version` ;
- `evidence` ;
- `warnings` ;
- `write_actions_allowed`.

### `AnalysisReport`

- fichiers inventoriés ;
- fichiers lisibles ;
- fichiers illisibles ;
- cartes analysées ;
- événements analysés ;
- pages analysées ;
- références valides/manquantes ;
- scripts dynamiques non exécutés ;
- couverture française ;
- limites ;
- statut analytique.

### `RepairAction`

- identifiant ;
- fichier cible relatif ;
- raison ;
- niveau de risque ;
- aperçu avant/après ;
- réversible ;
- validateur associé.

## 7. Migration progressive

1. Ajouter les tests autour du comportement existant.
2. Créer les modèles et le registre d'adaptateurs.
3. Envelopper le comportement Essentials actuel sans le réécrire.
4. Faire piloter l'interface par le résultat de détection.
5. Ajouter l'adaptateur `Unknown`.
6. Ajouter l'analyse profonde commune.
7. Ajouter les réparations sûres.
8. Introduire Flux expérimental seulement après stabilisation des garde-fous.
