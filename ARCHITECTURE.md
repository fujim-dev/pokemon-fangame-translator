# Architecture actuelle et cible

## 1. Vue d'ensemble de la v1.0.2

L'application est une application de bureau Python/Tkinter. La base stable v1.0.2
reste organisée autour de sept modules principaux, auxquels s'ajoute désormais la
première brique de migration v1.1 : le paquet `adapters`.

```text
Pokemon_Fangame_Translator.py
├── adapters/
│   ├── base.py
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
├── safe_io.py
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

### `structured_extractor.py`

Responsabilités :

- lecture structurée des cartes RPG Maker XP ;
- extraction des commandes de dialogue et de choix ;
- parcours de banques de messages ;
- extraction de certaines clés PBS ;
- création d'identifiants stables ;
- production des lignes CSV.

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

Encore partiel ou pas encore en place :

- branches dynamiques et références statiques avancées de l'analyse profonde ;
- autres réparations déterministes au-delà des commandes protégées simples ;
- exposition de l'import/réinjection Flux dans l'adaptateur et l'interface ; les
  composants internes existent mais restent volontairement inaccessibles à
  l'utilisateur ;
- validation d'un corpus traduit représentatif de toutes les sources Flux,
  tests de démarrage/jouabilité et multiplication des scénarios de rollback ;
- encapsulation complète de la stratégie de reconstruction dans chaque adaptateur.
- limite de temps des sondes et des analyses statiques : une sonde qui se bloque
  sans lever d'exception n'est pas encore interrompue automatiquement.

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
