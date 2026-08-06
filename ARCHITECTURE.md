# Architecture actuelle et cible

## 1. Vue d'ensemble de la v1.0.2

L'application est une application de bureau Python/Tkinter organisée autour de sept modules principaux.

```text
Pokemon_Fangame_Translator.py
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
- diagnostic heuristique ;
- affichage des résultats ;
- déclenchement de l'extraction ;
- ouverture des studios de traduction et de reconstruction ;
- génération de rapports ;
- export d'un diagnostic public.

Points importants :

- `Diagnostic` est une dataclass contenant les résultats de détection.
- `run_diagnostic()` identifie actuellement surtout les structures RPG Maker XP / Essentials.
- `merge_project_rows()` conserve les traductions précédentes grâce à `id_stable` et à une clé de secours.
- les projets sont stockés dans `Documents/Pokemon Fangame Translator/Projets`.

Risque architectural :

- ce fichier concentre l'interface, la détection, les rapports et une partie de la logique de projet ;
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
- il ne constitue pas encore une interface d'adaptateur générique.

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

Limite actuelle :

- moteur conçu pour les formats classiques ;
- aucune interface d'adaptateur ou de stratégie de reconstruction.

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
→ run_diagnostic()
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
- extraction des textes compatibles ;
- reconstruction FPK sécurisée ;
- validation de la structure ;
- blocage des versions inconnues.

Il ne doit pas dépendre d'un nom de dossier.

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
