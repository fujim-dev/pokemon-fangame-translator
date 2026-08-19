# Critères d'acceptation et plan de tests v1.1

## 1. Règles générales

Une fonction n'est pas acceptée seulement parce que l'interface l'affiche. Elle doit disposer de tests reproductibles et d'un rapport clair.

Les tests automatisés ne doivent utiliser aucun fichier propriétaire de fangame. Employer des fixtures synthétiques minimales.

## 2. Non-régression v1.0.2

### AC-001 — Démarrage de l'application

**Étant donné** les sources valides  
**Quand** la vérification des sources est lancée  
**Alors** tous les modules principaux se compilent sans erreur.

Commande :

```text
python build_support/verify_sources.py
```

### AC-002 — Projet persistant

Une nouvelle extraction d'une fixture conserve les traductions existantes lorsque `id_stable` correspond.

### AC-003 — Commandes protégées

Une traduction ne peut pas être marquée prête si elle supprime, ajoute ou altère une commande protégée non autorisée.

### AC-004 — Original intact

Après une reconstruction réussie ou échouée, toutes les empreintes du dossier original sont identiques aux empreintes initiales.

## 3. Détection d'adaptateur

### AC-101 — Essentials classique reconnu

Une fixture contenant les indices Essentials définis est associée à `PokemonEssentialsAdapter` avec une confiance suffisante.

### AC-102 — Flux reconnu

Une fixture Flux synthétique reconnue est associée à `PokemonFluxAdapter`.

### AC-103 — Format inconnu

Une structure non reconnue retourne `UnknownAdapter`, avec :

- traduction désactivée ;
- reconstruction désactivée ;
- analyse en lecture seule autorisée.

### AC-104 — Ambiguïté

Si deux adaptateurs obtiennent des scores trop proches, le résultat est ambigu et les actions d'écriture sont bloquées.

### AC-105 — Nom trompeur

Un dossier nommé « Pokemon Flux » sans structure Flux ne doit pas être reconnu comme Flux.

### AC-106 — Capacités de l'interface

L'interface ne présente que les actions autorisées par l'adaptateur.

### AC-107 — Racines et indices redirigés

Une racine de fangame, un fichier ou un dossier critique utilisé comme indice
de détection qui est un lien symbolique ou une jonction ne peut pas autoriser
l'extraction, la traduction ou la reconstruction. La détection reste en lecture
seule et un appel direct à l'extraction est également refusé.

### AC-108 — Échec d'une sonde d'adaptateur

Si une sonde échoue, le registre ne choisit pas silencieusement un autre moteur.
Il retourne un profil en lecture seule, signale une détection incomplète sans
exposer le détail brut de l'exception et bloque toutes les actions d'écriture.

### AC-109 — Inventaire d'extraction Essentials

Avant de parser une source Essentials, l'extracteur inventorie et empreinte les
marqueurs d'identité ainsi que les fichiers Data/PBS pris en charge. Un lien
symbolique, une jonction, un reparse point, un chemin Windows ambigu ou une
entrée spéciale dans les arborescences critiques bloque l'extraction.

### AC-110 — Résistance TOCTOU de l'extraction Essentials

Les fichiers sont copiés vers un instantané temporaire isolé par une lecture
contrôlée, puis seul cet instantané est parsé. Un fichier remplacé entre
l'inventaire et la copie, ou une source ajoutée, supprimée, réorientée ou modifiée
avant la vérification finale, invalide toute l'extraction. Aucun résultat partiel
n'est accepté.

### AC-111 — Provenance et publication cohérentes

Chaque ligne Essentials référence l'empreinte de son fichier et de l'inventaire
global. Le CSV, le rapport, le manifeste privé et l'identité du projet sont
publiés comme un lot : une panne tardive restaure les versions précédentes. Une
modification concurrente annule la publication et un CSV provenant d'un autre
inventaire est refusé avant reconstruction.

### AC-112 — Délai maximal et isolation des sondes

Chaque sonde s'exécute hors du processus Tkinter avec un délai maximal borné.
Une sonde expirée, annulée, morte ou invalide force le résultat global vers
`UnknownAdapter`, même si un autre adaptateur avait réussi. Le worker et ses
descendants sont terminés ; aucun résultat ou effet tardif ne peut modifier les
capacités déjà annoncées. Une fermeture ou un changement de dossier annule le
diagnostic actif sans laisser de processus de sonde résiduel.

### AC-113 — Profil structurel et version Essentials

La détection distingue la famille Essentials de son profil de capacité. Une
constante `Essentials::VERSION` présente dans le script `Settings` compressé est
lue par Marshal et zlib sans exécuter Ruby, puis comparée aux déclarations de
`Game.ini` et `mkxp.json`. Une contradiction, une version v20/future ou une
structure moderne insuffisamment confirmée donne `essentials_modified_or_unknown`
et bloque extraction, traduction et reconstruction. Un `PluginScripts.rxdata`
vide n'est jamais un indice de plugin et quelques PBS copiés dans un faux projet
RMXP ne suffisent pas à reconnaître Essentials.

### AC-114 — Essentials v21.1 en lecture seule du jeu

Le profil `essentials_v21_1_readonly` autorise l'analyse, l'extraction et le
cycle de vie du projet CSV, mais jamais `RECONSTRUCT` ni l'écriture dans le jeu.
Les événements communs ont des identifiants stables incluant leur occurrence et
les métriques d'analyse profonde correspondent au CSV extractible. Les schémas
PBS modernes pris en charge conservent commandes, casse, encodage, BOM, CRLF,
commentaires et ordre ; les sous-champs non reconstructibles restent explicitement
distingués. Le profil et la méthode de version font partie de la provenance.

### AC-115 — Candidat v21.1 borné sans activation générale

Le profil v21.1 reste privé de `RECONSTRUCT`. Une porte interne de validation ne
peut construire qu'un plan `accepted` contenant exactement une occurrence de
banque dans `Data/messages_game.dat`. Elle revalide le profil, le manifeste, le
CSV, l'identité, l'inventaire et les empreintes avant simulation puis avant copie.
Le Marshal produit doit être identique au candidat complet calculé en mémoire ;
les entrées non ciblées, leurs métadonnées et tous les fichiers hors plan restent
inchangés. Plusieurs occurrences, une carte, un PBS, un événement commun ou un
sous-champ `Point` sont refusés. Cette validation privée ne rend pas la
reconstruction v21.1 disponible dans l'interface.

### AC-116 — Corpus de banques et carte v21.1 toujours privés

Deux portées internes distinctes peuvent élargir la preuve sans accorder
`RECONSTRUCT`. La première exige exactement trois occurrences acceptées couvrant
une banque core directe, une banque game directe et une banque game imbriquée,
et cible exactement `messages_core.dat` et `messages_game.dat`. La seconde exige
exactement un dialogue et un choix acceptés sur la même page d'une carte
`MapXXX.rxdata`. Elle vérifie les classes RPG, les identifiants, index, codes,
indentations et paramètres, puis remplace le libellé du choix dans la commande 102
et dans son unique branche 402 correspondante. Une branche 402 absente,
dupliquée ou incohérente, ou des limites 101/401 impossibles à déduire sans
ambiguïté, bloque le candidat. Chaque fichier reconstruit doit être identique
au payload complet calculé en mémoire et tous les fichiers hors plan restent
inchangés. Ces preuves privées ne modifient jamais les capacités publiques du
profil v21.1.

Validation manuelle privée associée à AC-116 : le candidat v21.1 a été lancé et
l'affichage du dialogue 101/401 ainsi que celui du choix 102/402 ont été confirmés
dans le jeu ; le menu de choix est resté fonctionnel. Ce contrôle humain, sans
fichier ni contenu du jeu conservé dans le dépôt, ne généralise pas la portée du
réinjecteur et n'active pas `RECONSTRUCT`.

### AC-117 — Segmentation déterministe des dialogues 101/401

Chaque dialogue extrait possède une preuve immuable, dépourvue de texte, qui
décrit exactement ses commandes 101/401 : index, codes, indentation, paramètres,
empreintes et nombre de contrôles `\n` internes par segment. La réinjection privée
retrouve les frontières par l'ordre des contrôles protégés et ne modifie que le
paramètre textuel prévu. Les commandes voisines, autres paramètres, métadonnées,
indentations et ordre restent identiques. Une continuation 401 orpheline ou mal
indentée, un paramètre invalide, un nombre de contrôles différent, ou une preuve
absente/altérée bloque le cas. La même segmentation alimente l'extraction des
événements communs, sans accorder `RECONSTRUCT` au profil v21.1.

### AC-118 — Candidat synthétique d'événements communs v21.1

Une portée interne distincte exige exactement trois dialogues acceptés répartis
sur deux objets `RPG::CommonEvent` de `Data/CommonEvents.rxdata`, dont un dialogue
simple et un dialogue avec plusieurs 401 et contrôle `\n` interne. Chaque ligne
est liée à l'index du tableau Marshal, à `@id`, `@trigger`, `@switch_id`, à
l'empreinte complète de l'événement et à la preuve 101/401 extraite.

Avant toute mutation, toutes les occurrences sont revalidées ensemble et chaque
flux complet de commandes est contrôlé par le segmentateur partagé. ID/index,
trigger/switch, ordre, codes, indentations, paramètres non textuels, commandes
voisines, choix/branches et ivars restent inchangés. Les seules valeurs modifiées
sont les paramètres textuels explicitement ciblés. Un événement manquant ou
remplacé, une structure/empreinte modifiée, un 401 orphelin ou mal indenté, une
preuve incohérente, plusieurs occurrences incompatibles ou un chevauchement
bloquent le candidat avant la première écriture.

Après reconstruction sur copie, `CommonEvents.rxdata` doit être identique au
payload complet calculé en mémoire, aucun fichier hors plan ne doit changer et
la réextraction doit retrouver exactement les trois traductions. Ce test reste
synthétique et privé : il n'accorde aucune capacité publique, ne couvre pas les
sous-champs `Point` ou les PBS modernes et exige encore un round-trip réel sur
copie de travail avant tout élargissement.

### AC-119 — Validation réelle unitaire d'un événement commun v21.1

Une seconde portée interne, distincte du corpus AC-118, accepte exactement une
occurrence de dialogue commun composée d'une seule commande 101. Elle exige le
même profil v21.1 en lecture seule, le projet `accepted`, un événement nommé dont
l'index correspond à l'ID, un trigger/switch valide, l'empreinte complète de
l'objet et une preuve de segmentation à un seul segment. Toute autre occurrence,
un choix, une continuation 401 ou un autre fichier est refusé par cette portée.

Le parcours privé réel doit partir d'une copie byte-identical de la référence,
passer par l'extraction complète et la sauvegarde transactionnelle du Studio,
puis reconstruire une troisième copie. Parmi les fichiers provenant du jeu,
seul `Data/CommonEvents.rxdata` peut différer. La structure Marshal, tous les
événements et commandes non ciblés, les métadonnées de la commande cible et
l'identifiant stable réextrait doivent rester identiques. Une provenance modifiée
ou l'empreinte déjà altérée de l'événement doit être refusée.

Cette preuve a réussi techniquement sur la référence v21.1 : 30 402 occurrences,
7 606 fichiers de jeu contrôlés et une occurrence réextraite à la même identité.
La racine de sortie privée est limitée prudemment à 120 caractères : une racine
plus longue est refusée avant la copie, car le binaire mkxp-z de référence a
affiché une absence erronée de script à 145 caractères malgré des fichiers
`Game.ini` et `Scripts.rxdata` intacts. Le candidat reconstruit sous une racine
courte a atteint l'écran titre, puis une validation humaine a confirmé en jeu
l'affichage complet de la traduction ciblée lors du déclenchement réel de
l'événement commun. Cette preuve reste strictement limitée à cette occurrence
simple composée d'une commande 101 : elle ne valide pas des continuations 401
réelles, des choix 102/402 d'événements communs, les sous-champs `Point` ou les
PBS modernes. Elle ne conserve aucun contenu privé dans le dépôt et ne confère
toujours pas `RECONSTRUCT` au profil public.

### AC-120 — Validation réelle bornée d'une continuation 401 commune

Une troisième portée interne accepte exactement une occurrence de dialogue
commun composée de deux segments contigus : une commande 101 suivie d'une seule
commande 401 de même indentation. Elle exige la preuve immuable issue du
segmentateur partagé, les index et codes exacts, les empreintes des commandes et
paramètres, le nombre de contrôles `\n` internes et l'empreinte complète de
l'événement. Une 401 manquante, supplémentaire, déplacée ou mal indentée, un
paramètre non textuel modifié, une preuve altérée ou une provenance incohérente
bloquent la portée avant toute copie.

La preuve technique réelle a réussi sur une copie séparée de la référence v21.1 :
30 402 occurrences extraites, sauvegarde/réouverture transactionnelles du Studio,
seconde session refusée, simulation privée, reconstruction et réextraction à la
même identité stable. Parmi les 7 606 fichiers du jeu, seul
`Data/CommonEvents.rxdata` diffère ; les trois fichiers d'accompagnement PFT sont
ajoutés séparément. La référence et la copie de travail restent inchangées, la
structure 101/401 est conservée et le candidat sous chemin court atteint une
fenêtre de titre v21.1 sans modifier ses fichiers.

Une validation visuelle humaine a ensuite confirmé dans le jeu l'affichage
complet de la traduction reconstruite pour cette unique occurrence réelle 101 +
exactement une continuation 401. Cette preuve ne couvre aucun CommonEvent avec
plusieurs 401, aucun choix commun 102/402, aucun sous-champ `Point` ni PBS moderne.
Elle n'active pas `RECONSTRUCT` et ne conserve aucun contenu du jeu dans le dépôt.

La référence v21.1 inspectée ne contient aucune séquence commune réelle composée
d'une commande 101 suivie d'au moins deux continuations 401. Cette forme reste
couverte uniquement par AC-117/AC-118 sur fixtures synthétiques et devra recevoir
une preuve réelle distincte si un corpus approprié devient disponible ; aucune
occurrence n'a été fabriquée dans la référence.

### AC-121 — Candidat réel borné d'un choix commun 102/402

Une quatrième portée interne accepte exactement un libellé de choix commun 102 et
sa branche 402 correspondante. L'extraction doit enregistrer l'index de la 102, le
sous-index du libellé, l'index de l'unique 402, son paramètre textuel, l'indentation,
l'index/ID, le trigger/switch et l'empreinte Marshal complète de l'événement. La
réinjection remplace uniquement la chaîne dans le tableau de choix 102 et sa copie
dans le paramètre 1 de la branche 402.

Les fixtures synthétiques refusent une branche absente ou supplémentaire, un
sous-index, ordre, indentation ou paramètre non textuel modifié, ainsi qu'une
preuve ou provenance altérée. Elles vérifient aussi que la logique 102/402 partagée
avec les cartes ne régresse pas et que les autres choix et commandes restent
byte-identical.

La preuve technique réelle a réussi sur une copie séparée de la référence v21.1 :
30 402 occurrences extraites, dont 18 occurrences CommonEvents et 4 choix, session
Studio exclusive, sauvegarde/reprise transactionnelles, simulation, reconstruction
et réextraction à la même identité stable. Parmi les fichiers du jeu, seul
`Data/CommonEvents.rxdata` diffère ; les trois fichiers d'accompagnement PFT sont
ajoutés séparément. La référence et la copie de travail sont inchangées, tous les
autres choix, branches et commandes restent identiques, et le candidat court
atteint l'écran titre sans modifier ses fichiers.

Une validation visuelle humaine a ensuite confirmé l'affichage complet du marqueur
sur ce choix réel, sa sélection, puis la navigation normale dans la branche 402
correspondante jusqu'à sa fin. Cette preuve ne couvre qu'un seul choix et cette
branche correspondante : elle ne valide pas plusieurs 401 réelles, d'autres formes
de choix communs, `Point`, les PBS modernes ni la reconstruction publique v21.1.
`RECONSTRUCT` reste absent.

### AC-122 — Choix commun réel de sous-index non nul

La même porte privée doit accepter une occurrence unique dont le choix ciblé a un
sous-index strictement supérieur à zéro et dont la branche 402 n'est pas
nécessairement adjacente à la commande 102. La preuve exige l'ordre complet des
choix, le sous-index exact, l'unique branche correspondante, son indentation, les
paramètres non textuels et une fermeture 404 de même indentation.

Les fixtures synthétiques couvrent le sous-index 1 et refusent un mauvais
sous-index, des branches échangées, absentes ou supplémentaires, un ordre de choix
modifié, le changement d'un autre choix, une indentation ou un paramètre hors texte
différent, une fermeture 404 altérée, ainsi qu'une preuve ou provenance incohérente.
Le round-trip vérifie que le premier choix et sa branche restent byte-identical et
que seules les deux chaînes du choix ciblé et de sa branche changent.

La preuve technique réelle a réussi sur une copie séparée de la référence v21.1 :
une occurrence de sous-index 1 associée à une branche 402 éloignée a traversé
l'extraction complète, le Studio transactionnel, la simulation, la reconstruction
privée et la réextraction à la même identité stable. Seul
`Data/CommonEvents.rxdata` diffère parmi les fichiers du jeu ; la référence et la
copie de travail restent inchangées, les autres choix/branches et la fermeture 404
sont préservés, et le candidat court atteint l'écran titre sans modifier ses
fichiers.

Une validation visuelle humaine a ensuite confirmé que le libellé modifié
s'affiche sur le second choix, que sa sélection exécute bien la branche 402 de
sous-index 1 sans entrer dans la première branche, puis que l'appel se termine
normalement. Cette preuve ne s'étend pas aux autres structures 102/402, au
multi-401 réel, à `Point`, aux PBS modernes ou à la reconstruction publique.
`RECONSTRUCT` reste absent.

### AC-123 — Premier sous-champ Point v21.1 strictement borné

L'extraction d'un `Point` de `PBS/town_map.txt` enregistre une preuve immuable et
sans texte : ligne, section, occurrence de clé, nombre et position des champs,
limites et espaces de chaque champ, offsets des virgules, fin de ligne et
empreintes du préfixe, de la valeur, des champs non ciblés, de la ligne et du
fichier complet. Ces métadonnées font partie de la structure immuable du projet
Studio.

Une porte privée distincte accepte exactement un `Point.Name` composé de trois
sous-champs simples (`x`, `y`, texte), le texte étant obligatoirement le troisième.
Elle exige `PBS/town_map.txt`, UTF-8 avec BOM et CRLF, une provenance v21.1 intacte
et une traduction sans guillemet, virgule ni retour de ligne. La simulation
reconstruit le fichier en mémoire ; la copie candidate ne remplace que les octets
du sous-champ ciblé, puis une réextraction doit retrouver le même identifiant,
sous-index, nombre de champs et texte traduit.

Les fixtures refusent un sous-champ manquant ou supplémentaire, un ordre ou une
valeur non textuelle modifiés, un autre séparateur, un commentaire déplacé, un
espace significatif différent, un BOM ou des CRLF modifiés, une preuve/provenance
altérée et une source changée après planification. Le round-trip réel a ensuite
extrait 30 402 occurrences, dont 27 noms et 8 descriptions Point, traversé la
session Studio exclusive et sa reprise, puis modifié uniquement
`PBS/town_map.txt` dans le candidat. La référence et la copie de travail sont
restées identiques et la réextraction a retrouvé exactement l'occurrence ciblée.

Cette preuve est structurelle et limitée à un seul `Point.Name` réel à trois
champs. Elle ne valide ni `Point.Description`, ni les formes à quatre, sept ou
huit champs, ni les lignes citées/ambiguës, ni la compilation PBS vers les données
utilisées en jeu. Aucune validation visuelle n'en est déduite et `RECONSTRUCT`
v21.1 public reste absent.

### AC-124 — Synchronisation privée de TownMap PBS et compilé

L'analyse statique de la référence v21.1 relie de manière déterministe la
N-ième affectation `Point` d'une section PBS à
`Hash[section].@point[N-1]` dans `Data/town_map.dat`. La racine Marshal 4.8 est un
`Hash<Integer, GameData::TownMap>` ; chaque objet conserve exactement `@id`,
`@real_name`, `@filename`, `@point`, `@flags` et `@pbs_file_suffix`. Chaque Point
compilé est un `Array` de huit positions : coordonnées entières, nom et
description optionnelle sous forme de `RubyString` UTF-8 `E=true`, puis paramètres
optionnels entiers ou `nil`. Les champs PBS absents ou vides sont normalisés en
`nil` par la compilation ; cette normalisation ne change ni la section ni l'ordre
des occurrences.

Avant d'attacher une preuve à une occurrence, l'extraction exige l'égalité de
l'ensemble des sections, des identifiants, noms, fichiers graphiques et nombres
de Point entre PBS et donnée compilée. Elle prouve également que la lecture puis
l'écriture Marshal reproduit le fichier original octet pour octet. Une preuve
sans texte lie ensuite les empreintes des deux fichiers, le chemin compilé, les
types, l'ordre, le graphe complet et le nombre de références de la chaîne ciblée.
Ces métadonnées sont immuables dans le Studio.

La porte privée AC-123 synchronise désormais exactement une occurrence
`Point.Name` réelle à trois champs dans `PBS/town_map.txt` et
`Data/town_map.dat`. Elle recontrôle les deux empreintes après planification,
construit les deux fichiers en mémoire, relit le Marshal et refuse la publication
si le graphe masqué hors cible, les types, la valeur source ou la provenance ont
changé. Les fixtures refusent notamment section/index/type erroné, section ou
Point ajouté/manquant, paramètre non textuel modifié, source PBS ou compilée
changée après simulation et preuve/provenance altérée.

Le round-trip réel a extrait 30 402 occurrences et lié les 35 sous-champs Point
aux données compilées. Après Studio, reprise, simulation, reconstruction et
réextraction, seuls `PBS/town_map.txt` et `Data/town_map.dat` diffèrent dans le
candidat ; l'original et la copie de travail sont inchangés, tous les autres
fichiers sont identiques et le graphe Marshal hors texte ciblé est strictement
identique. Le candidat démarre sans erreur immédiate et reste réactif. Une
validation visuelle humaine a ensuite confirmé dans le jeu l'affichage exact du
`Point.Name` traduit sur la carte régionale. Cette preuve reste strictement
limitée à cette occurrence réelle simple à trois champs, synchronisée entre
`PBS/town_map.txt` et `Data/town_map.dat` ; elle ne couvre ni
`Point.Description`, ni les formes PBS à quatre, sept ou huit champs, ni un autre
PBS moderne. `RECONSTRUCT` v21.1 public reste absent.

### AC-125 — Premier Point.Description v21.1 strictement borné

Une sixième porte privée, distincte de celle du nom, accepte exactement une
occurrence `Point.Description` simple à quatre champs (`x`, `y`, nom,
description). Le sous-champ ciblé doit être le quatrième et la représentation
compilée doit être un tableau de huit positions composé de deux entiers, deux
`RubyString` UTF-8 `E=true`, puis exactement quatre `nil`. La forme PBS, le nom,
les coordonnées, les valeurs hors cible et les deux fichiers complets font partie
de la preuve immuable.

La simulation et la reconstruction synchronisent uniquement
`PBS/town_map.txt` et `Data/town_map.dat`. Elles refusent un sous-index ou un
nombre de champs différent, une description ajoutée/supprimée, un nom, une
coordonnée ou un paramètre numérique modifié, un type ou une structure Marshal
différents, une source changée après planification et toute divergence de
provenance. Une description à sept champs reste explicitement hors de cette
porte, même si son extraction est déterministe.

Le round-trip réel a confirmé la bijection des huit descriptions de la référence
v21.1 : six formes à quatre champs et deux formes à sept champs, toutes liées à
une `RubyString` compilée unique. Une seule forme à quatre champs a traversé le
Studio, la reprise, la simulation, la reconstruction et la réextraction. Les
30 402 occurrences sont conservées, seuls les deux fichiers TownMap attendus
diffèrent, le graphe Marshal hors description ciblée est identique, et
l'original comme la copie de travail restent inchangés. Le candidat sous chemin
court démarre et reste actif sans erreur immédiate. Une validation visuelle
humaine a ensuite confirmé l'affichage exact de cette description reconstruite
sur la carte régionale. Cette preuve ne couvre qu'un seul `Point.Description`
réel sous la forme PBS à quatre champs `x,y,nom,description` ; elle ne couvre pas
les autres descriptions, les formes 7/8 champs ni la reconstruction publique.
`RECONSTRUCT` v21.1 reste absent.

### AC-126 — Première description Point v21.1 à sept champs

Une septième portée privée accepte exactement une occurrence
`Point.Description` sous la forme PBS à sept champs : coordonnées régionales,
nom, description, puis identifiant de carte et coordonnées de destination. Le
schéma statique v21.1 `^uusSUUUU` et les usages de la carte régionale confirment
que les trois derniers champs présents forment la destination `[map ID, x, y]` ;
la huitième position compilée, réservée au switch optionnel, doit rester `nil`.

La portée exige exactement les types Marshal `Integer`, `Integer`, deux
`RubyString`, trois `Integer`, puis `nil`. Elle lie le sous-champ description à
sa ligne PBS complète, aux sept champs et à leurs six séparateurs, à l'empreinte des deux
fichiers, au chemin compilé et au graphe Marshal hors cible. Toute modification
du nombre ou de l'ordre des champs, des coordonnées, du nom, d'une autre
description, des trois entiers, du `nil`, d'un type Ruby, de la structure, de la
preuve ou de la provenance bloque l'opération.

L'inventaire réel contient six Point à sept champs : deux descriptions présentes
et quatre descriptions absentes, normalisées en `nil`, tous avec trois entiers
de destination et un switch compilé `nil`. Un seul des deux cas portant une
description a traversé le Studio, la reprise, la simulation, la reconstruction
privée et la réextraction parmi 30 402 occurrences. Seuls
`PBS/town_map.txt` et `Data/town_map.dat` diffèrent dans le candidat ; l'original,
la copie de travail et le graphe Marshal hors chaîne ciblée restent identiques.
Le candidat court démarre sans erreur immédiate. La validation visuelle humaine
a ensuite confirmé dans le jeu l'affichage exact de la description reconstruite
sur la carte régionale. Cette preuve reste strictement limitée à cette unique
occurrence réelle à sept champs ; les cinq autres occurrences à sept champs, les
formes à huit champs et `RECONSTRUCT` v21.1 public restent bloqués.

### AC-127 — Premier Point.Name v21.1 à huit champs

Une huitième portée privée accepte exactement une occurrence `Point.Name` sous
la forme PBS à huit champs : deux coordonnées régionales, un nom, une
description absente, trois paramètres de destination absents et un identifiant
de switch de visibilité. Le schéma statique v21.1 `^uusSUUUU` et les trois
lectures de `point[7]` dans l'interface de carte prouvent que le dernier entier
conditionne l'affichage du nom, de la description et de la destination.

La portée exige exactement `Integer`, `Integer`, `RubyString`, quatre `nil`,
puis `Integer`. Elle conserve les huit positions, les sept séparateurs, le BOM,
les CRLF, le chemin compilé, les valeurs absentes et le graphe Marshal hors nom
ciblé. Les fixtures refusent notamment 8→7, l'emploi inverse d'une forme à sept
champs, un champ supplémentaire, une permutation, un autre switch, un type Ruby
différent, `nil` devenu valeur ou inversement, des coordonnées, une destination,
un autre nom, la structure, les sources ou la provenance modifiés.

L'inventaire réel contient deux Point à huit champs de cette forme. Une seule
occurrence a traversé l'extraction de 30 402 lignes, le verrou et la reprise du
Studio, la simulation, la reconstruction privée synchronisée et la réextraction
à la même identité stable. Seuls `PBS/town_map.txt` et `Data/town_map.dat`
diffèrent parmi les fichiers du jeu ; l'original, la copie de travail, le switch
et le graphe Marshal hors chaîne ciblée restent identiques. Le candidat sous
chemin court démarre sans erreur mkxp-z. Une validation visuelle humaine a
ensuite confirmé dans le jeu l'affichage exact du nom reconstruit sur la carte
régionale, après activation du switch de visibilité réel ciblé. Cette preuve
reste strictement limitée à cette unique occurrence réelle à huit champs : la
seconde occurrence réelle, tout autre corpus et `RECONSTRUCT` v21.1 public ne
sont pas validés.

### AC-128 — Premier message téléphone v21.1 synchronisé

L'analyse strictement statique relie les 41 occurrences réelles de
`PBS/phone.txt` aux objets `GameData::PhoneMessage` de `Data/phone.dat`, puis à
la banque `PHONE_MESSAGES` située à l'index 22 de
`Data/messages_game.dat`. Elle exige une correspondance bijective, l'ordre et
les types Marshal exacts, les tableaux et `nil` attendus, des chaînes non
partagées et un aller-retour Marshal byte-identical. Aucun script Ruby n'est
exécuté.

Une portée privée accepte exactement une occurrence `End`, avec sa provenance,
sa ligne PBS avec BOM UTF-8 et CRLF, son chemin dans `phone.dat` et son entrée
de banque d'exécution. Les trois fichiers sont calculés en mémoire puis publiés
dans le candidat par un lot transactionnel unique. Toute divergence de source,
preuve, type, ordre, empreinte ou occurrence annule l'opération ; une panne de
publication injectée restaure exactement les trois fichiers précédents.

Le round-trip réel a conservé 30 402 occurrences, dont les 41 messages
téléphone, traversé le Studio, sa sauvegarde et sa reprise, puis modifié
uniquement `PBS/phone.txt`, `Data/phone.dat` et
`Data/messages_game.dat`. La réextraction retrouve la traduction exacte dans
les trois représentations ; la référence et la copie de travail restent
identiques sur 7 606 fichiers. Le candidat sous chemin court démarre sans erreur
immédiate. Une validation visuelle humaine a confirmé l'affichage complet du
marqueur `[TEST PFT v21.1 PHONE END]` sur cette occurrence réelle et la fin
normale de l'appel. Cette preuve ne couvre aucun autre message, contact ou
schéma téléphone et n'active pas `RECONSTRUCT` v21.1 public.

### AC-129 — Premier LoseText de dresseur v21.1 synchronisé

L'analyse strictement statique relie les `LoseText` visibles de
`PBS/trainers.txt` aux objets `GameData::Trainer` de `Data/trainers.dat`, puis
à la banque d'exécution `TRAINER_SPEECHES_LOSE` située à l'index 23 de
`Data/messages_game.dat`. Elle préserve l'identité type/nom/version du
dresseur, les ivars et types Marshal, l'ordre des sections, les références de
chaînes et les graphes hors cible. La valeur spéciale non visible `...` est
conservée mais n'est pas présentée comme un texte traduisible.

Une portée privée accepte exactement un `LoseText` réel, unique et non partagé.
Elle vérifie la provenance, les trois empreintes sources, les preuves PBS,
compilée et d'exécution, puis calcule les trois fichiers en mémoire avant une
publication transactionnelle unique. Une occurrence partagée, une identité ou
une structure modifiée, un contrôle protégé divergent ou une panne de
publication entraîne un refus ou le rollback exact des fichiers déjà remplacés.

La preuve réelle a inventorié 20 occurrences structurelles, dont 19 visibles et
7 uniques admissibles à cette porte privée. Le round-trip a conservé les
30 402 occurrences extraites, traversé le Studio, sa sauvegarde et sa reprise,
puis modifié uniquement `PBS/trainers.txt`, `Data/trainers.dat` et
`Data/messages_game.dat`. La réextraction retrouve la traduction sous la même
identité stable dans les trois représentations ; la référence et la copie de
travail restent inchangées. Une validation visuelle humaine a confirmé dans un
combat réel l'affichage complet du marqueur
`[TEST PFT v21.1 TRAINER LOSE]`, puis la fin normale du dialogue et le retour au
jeu. Cette preuve ne couvre aucun autre dresseur, texte partagé, schéma de
dresseur ou banque de combat et n'active pas `RECONSTRUCT` v21.1 public.

### AC-130 — Première Ability.Description v21.1 synchronisée

L'analyse strictement statique relie les 267 `Description` réelles de
`PBS/abilities.txt` aux objets `GameData::Ability` de `Data/abilities.dat`, puis
à la banque core `ABILITY_DESCRIPTIONS` située à l'index 11 de
`Data/messages_core.dat`. Elle conserve l'identifiant, le nom, les flags, le
suffixe PBS, les cinq ivars et types Marshal, l'ordre du registre, les
références de chaînes et les graphes hors cible. Les 27 occurrences portant un
texte partagé restent extractibles, mais sont refusées par la reconstruction
privée ; 240 occurrences uniques satisfont cette première preuve structurelle.

Une portée privée accepte exactement une description réelle, unique et non
partagée. Elle vérifie la provenance et les trois empreintes, recalcule les trois
fichiers en mémoire et les publie dans un lot transactionnel. Une identité, un
flag, une métadonnée hors cible, une preuve, une source après planification ou
une référence Marshal divergente bloque l'opération. Une panne de publication
injectée restaure exactement les fichiers déjà remplacés.

Le round-trip réel a conservé les 30 402 occurrences, traversé le Studio, sa
sauvegarde et sa reprise, puis modifié uniquement `PBS/abilities.txt`,
`Data/abilities.dat` et `Data/messages_core.dat`. La réextraction retrouve la
chaîne complète sous la même identité stable dans les trois représentations ;
la référence et la copie de travail restent inchangées. La validation humaine a
confirmé sur la bonne fiche la capacité ciblée, sa description originale et le
début visible du marqueur `[TEST PFT ...`, puis la fermeture normale de la fiche
et le retour au jeu. L'interface tronque la fin faute de place : cette observation
ne prouve donc pas l'affichage visuel du marqueur complet, seulement son préfixe,
même si sa présence complète est démontrée par la réextraction technique.

Cette preuve ne couvre aucun autre texte ou objet de capacité, aucune description
partagée, aucun autre registre `GameData` et n'active pas `RECONSTRUCT` v21.1
public.

### AC-131 — Première Species.Pokedex v21.1 synchronisée

L'analyse strictement statique relie les 898 `Pokedex` de base de
`PBS/pokemon.txt` et les 339 formes de `PBS/pokemon_forms.txt` aux 1 237 objets
`GameData::Species` de `Data/species.dat`, puis à la banque core
`POKEDEX_ENTRIES` située à l'index 3 de `Data/messages_core.dat`. La preuve exige
l'ordre exact des deux PBS dans le registre compilé, les identités `id/species/form`,
les ivars et types Marshal, les 136 textes de forme explicites et les 203 textes
hérités par partage d'objet. Les 48 entrées de base partageant leur texte restent
extractibles mais sont refusées ; 850 entrées uniques satisfont cette première
preuve structurelle.

Une portée privée accepte exactement une entrée de base unique, non héritée et
non partagée. Elle surveille les empreintes des deux PBS, de `species.dat` et de
la banque core, mais calcule et publie uniquement les trois fichiers réellement
ciblés dans un lot transactionnel. Une identité composée, un ordre, un héritage,
une métadonnée hors cible, une preuve ou une source modifiés bloque l'opération ;
une panne de publication injectée restaure les fichiers déjà remplacés.

Le round-trip réel a conservé les 30 402 occurrences, traversé le Studio, sa
sauvegarde et sa reprise, puis modifié uniquement `PBS/pokemon.txt`,
`Data/species.dat` et `Data/messages_core.dat`. `PBS/pokemon_forms.txt` est resté
byte-identical ; la référence et la copie de travail sont inchangées. La
réextraction retrouve la chaîne complète sous la même identité stable dans les
trois représentations et le candidat démarre sans modifier ses fichiers.

Une validation humaine a confirmé l'affichage clair et intégral du marqueur
`[TEST PFT v21.1 POKEDEX]`, immédiatement suivi du texte normal de l'entrée, puis
la fermeture fonctionnelle du Pokédex et le retour au jeu sans erreur. Cette
preuve reste limitée à cette occurrence réelle : elle ne couvre aucune entrée
héritée ou partagée, aucun autre texte ou champ d'espèce, aucun autre corpus et
n'active pas `RECONSTRUCT` v21.1 public.

### AC-132 — Premier MapMetadata.Name v21.1 synchronisé

L'analyse strictement statique relie les 69 champs `Name` de
`PBS/map_metadata.txt` aux clés numériques et objets `GameData::MapMetadata` de
`Data/map_metadata.dat`, puis à la banque `MAP_NAMES` située à l'index 21 de
`Data/messages_game.dat`. Elle exige l'identifiant numérique canonique, l'ordre
exact des sections et du Hash compilé, les 25 ivars et types Marshal attendus,
les chemins et empreintes des trois sources ainsi qu'un aller-retour Marshal
byte-identical. Aucun script Ruby n'est exécuté.

Une portée privée accepte exactement une occurrence `Name` réelle, unique et
non partagée entre les sections de cartes. Elle vérifie la provenance, les trois
empreintes, l'identité numérique, les preuves PBS, compilée et d'exécution, puis
calcule les trois fichiers en mémoire avant leur publication transactionnelle.
Un nom partagé, une collision de banque, un identifiant ou une structure
modifiés, une preuve obsolète ou une panne de publication entraîne un refus ou
le rollback exact des fichiers déjà remplacés.

La clé Ruby réelle ciblée dans `MAP_NAMES` est aussi référencée comme clé dans
une autre banque du même graphe Marshal. La porte ne modifie pas cet objet
partagé : elle remplace seulement la paire ciblée dans `MAP_NAMES` et compare le
graphe entier privé de cette paire avant/après. Les fixtures synthétiques
reproduisent cet alias et vérifient que l'autre banque reste structurellement
identique, tout en refusant une preuve, une source ou une provenance altérée.

Le round-trip réel a conservé les 30 402 occurrences, traversé le Studio, sa
sauvegarde et sa reprise, puis modifié uniquement `PBS/map_metadata.txt`,
`Data/map_metadata.dat` et `Data/messages_game.dat`. La réextraction retrouve la
traduction exacte sous le même identifiant stable dans les trois
représentations ; la référence et la copie de travail restent inchangées sur
7 606 fichiers et le candidat atteint l'écran titre sans erreur mkxp-z.

Une validation humaine a ensuite confirmé, lors de l'entrée sur la carte réelle
ciblée, l'affichage clair et intégral du bandeau
`Route 2 [TEST PFT v21.1 MAP]`. Cette preuve reste strictement limitée à cette
occurrence réelle de `MapMetadata.Name` : elle ne couvre aucun nom partagé,
aucun autre nom ou champ `MapMetadata`, aucun autre corpus et n'active pas
`RECONSTRUCT` v21.1 public.

### AC-133 — Première Move.Description v21.1 synchronisée

L'analyse statique relie les 740 sections de `PBS/moves.txt` aux objets
`GameData::Move` de `Data/moves.dat`, puis aux banques `MOVE_NAMES` et
`MOVE_DESCRIPTIONS` de `Data/messages_core.dat`. Seuls `Name` et `Description`
sont extractibles. La clé `Category` de ce fichier est validée comme l'enum
technique Physical/Special/Status, avec les codes compilés 0/1/2 ; elle ne doit
produire aucune ligne traduisible. Les clés homonymes réellement textuelles des
autres PBS restent inchangées.

La porte privée exige exactement une `Description` unique, trois empreintes
intactes, les 14 ivars de `GameData::Move`, tous les champs techniques, la
catégorie numérique, les chemins de banques et l'absence d'alias incompatible.
Une modification de `Category`, d'un autre paramètre, de la structure Marshal,
du BOM/CRLF, d'une preuve ou de la provenance doit bloquer la simulation. Une
panne pendant la publication des trois représentations doit restaurer leur état
exact.

Le round-trip réel borné a produit 29 662 occurrences utiles, dont 740 noms et
740 descriptions de Moves et aucune fausse `Category`. Il a traversé le Studio,
sa sauvegarde et sa reprise, modifié uniquement `PBS/moves.txt`,
`Data/moves.dat` et `Data/messages_core.dat`, puis réextrait le marqueur exact
sous la même identité stable. La référence et la copie de travail sont restées
inchangées sur 7 606 fichiers. Le candidat court atteint l'écran titre sans
erreur mkxp-z et sans modifier ses fichiers. Une validation humaine a ensuite
confirmé dans Bulbasaur > Summary > Moves que `TACKLE` conserve son nom et que
le marqueur complet `[TEST PFT v21.1 MOVE DESCRIPTION]` est visible dans sa
description, réparti sur deux lignes par l'interface. L'écran Moves est resté
fonctionnel et sa fermeture a permis le retour normal au jeu, sans erreur.

Cette preuve est strictement limitée à cette occurrence réelle de
`Move.Description` : elle ne couvre aucune autre Move, aucun `Move.Name`, les
descriptions partagées/aliasées ni aucune autre structure. `Category` demeure
une donnée technique non traduisible et `RECONSTRUCT` v21.1 public reste
désactivé.

## 4. Analyse profonde

### AC-201 — Aucun script Ruby exécuté

Une fixture contenant un script Ruby ayant un effet de bord ne doit jamais l'exécuter. Le rapport doit seulement signaler sa présence.

### AC-202 — Toutes les cartes lisibles analysées

Le nombre de cartes analysées correspond au nombre de cartes synthétiques lisibles de la fixture.

### AC-203 — Pages d'événements

Toutes les pages d'événements lisibles sont comptées, même lorsqu'elles sont conditionnelles.

### AC-204 — Fichier illisible

Un fichier invalide est signalé sans interrompre l'analyse des autres fichiers.

### AC-205 — Référence manquante

Une référence statique à une ressource absente produit une alerte contenant un chemin relatif, jamais un chemin personnel complet.

### AC-206 — Limites explicites

Le rapport contient une section indiquant les scripts dynamiques et formats non vérifiés.

## 5. Couverture française

### AC-301 — Ligne française

Une phrase française claire est classée française.

### AC-302 — Ligne anglaise

Une phrase anglaise claire est classée probablement anglaise.

### AC-303 — Ligne mixte

Une phrase contenant des segments anglais et français significatifs est classée mixte.

### AC-304 — Nom propre

Une valeur courte ou un nom propre ne doit pas être automatiquement compté comme échec de traduction ; elle est ambiguë.

### AC-305 — Commandes exclues

Les commandes techniques ne faussent pas les métriques de langue.

### AC-306 — Structures non analysées

Le rapport ne peut pas annoncer 100 % global si une source de texte connue n'a pas pu être analysée.

### AC-307 — Reproductibilité

Le même dossier et les mêmes options produisent les mêmes métriques.

## 6. Reconstruction et intégrité

### AC-401 — Écriture dans une copie

La reconstruction refuse une cible identique ou incluse dans le dossier original si cela présente un risque d'écrasement.

### AC-402 — Écriture atomique

Une interruption simulée pendant l'écriture ne laisse pas le fichier cible principal dans un état partiellement écrit.

### AC-403 — Relecture

Tout fichier modifié compatible est relu après écriture.

### AC-404 — Encodage UTF-8

Une traduction contenant des caractères accentués est enregistrée dans un format que le lecteur de validation peut relire.

### AC-405 — Fichier non ciblé

Un fichier qui ne figure pas dans le plan ne doit pas changer dans la copie, sauf métadonnées explicitement documentées.

### AC-406 — Validation échouée

Si la relecture échoue, le résultat est un échec ; aucun statut vert ne peut être affiché.

### AC-407 — Rapport d'empreintes

Le rapport indique les empreintes avant/après des fichiers ciblés sans exposer de contenu textuel.

### AC-408 — Projet rattaché au fangame

La reconstruction refuse un CSV associé à un autre chemin de jeu ou à un autre adaptateur.

### AC-409 — Projet inchangé depuis la simulation

Toute modification du CSV ou de son identité après la simulation bloque la copie et exige une nouvelle simulation.

### AC-410 — Échec de finalisation

Si le manifeste, le rapport ou un guide final ne peut pas être écrit, la copie porte un marqueur
`RECONSTRUCTION_INCOMPLETE.txt` et aucun succès n'est annoncé.

### AC-411 — Réextraction non destructive

Une extraction vide ou un ancien CSV illisible/incompatible ne remplace jamais le projet existant.

### AC-412 — Fichiers persistants du studio protégés

Une interruption d'écriture conserve la version précédente du glossaire, de la
mémoire de corrections et de l'état de reprise. Un fichier illisible ou des
corrections contradictoires sont signalés sans remplacement silencieux.

### AC-413 — Cycle de vie vérifiable du Studio Essentials

À l'ouverture, le Studio relie le CSV principal à l'identité, au manifeste, au
CSV témoin et au rapport de la même extraction. Une modification externe, un
remplacement à octets identiques, un artefact absent ou incohérent, une seconde
session ou une reprise rattachée à un autre état bloque les écritures. Le CSV,
son état révisionné et la reprise sont publiés dans un même lot avec rollback.
Un projet ancien sans manifeste reste consultable mais ne peut être sauvegardé,
repris ni reconstruit avant une nouvelle extraction vérifiée.

## 7. Réparation assistée

### AC-501 — Plan avant application

Aucune réparation n'est appliquée avant l'affichage ou l'enregistrement d'un plan.

### AC-502 — Réparation sûre

Une commande protégée simple manquante est restaurée correctement lorsque la position est non ambiguë.

### AC-503 — Cas ambigu

Une réparation ambiguë est refusée et marquée « vérification humaine requise ».

### AC-504 — Point de restauration

Un point de restauration est créé avant toute réparation.

### AC-505 — Rollback

Après un échec de validation, les fichiers de la copie retrouvent exactement leurs empreintes précédentes.

### AC-506 — Journal

Le journal précise chaque action réalisée sans contenir les dialogues complets par défaut.

### AC-507 — Réparation transactionnelle d'un projet Essentials

Pour un projet Essentials à provenance vérifiée, le plan est lié à l'empreinte
exacte du CSV et reste surveillé pendant l'application. Le CSV réparé ou restauré,
son état révisionné, une reprise inactive, la sauvegarde exacte et le journal sont
publiés sous le verrou de session dans un seul lot. Une modification externe du
CSV, du plan ou de la sauvegarde annule l'opération. Une panne restaure tous les
artefacts déjà remplacés ; si ce rollback échoue lui-même, les fichiers exacts de
récupération sont conservés et le projet n'est plus annoncé comme utilisable.
Les anciennes écritures directes refusent un projet Essentials rattaché et un
projet sans provenance fiable doit être réextrait.

## 8. Adaptateur Flux expérimental

### AC-601 — Version reconnue

La reconstruction Flux n'est autorisée que lorsque la signature et la version sont reconnues.

### AC-602 — Version inconnue

Une version inconnue peut être inventoriée en lecture seule, mais l'écriture est bloquée.

### AC-603 — Isolation

Le chemin Flux n'appelle pas le moteur PBS/RXDATA classique.

### AC-604 — Archive relue

L'archive Flux reconstruite doit pouvoir être relue par le validateur Flux.

### AC-605 — Original Flux intact

L'archive originale conserve son empreinte.

### AC-606 — Extraction Flux fidèle et déterministe

Deux extractions de la même fixture produisent les mêmes lignes, les mêmes
identifiants d'occurrence et les mêmes empreintes de texte.

### AC-607 — Occurrences Flux non ambiguës

Deux textes identiques situés à des chemins structurels différents possèdent
des identifiants distincts. Les commandes et balises sont conservées à l'identique.

### AC-608 — CSV commun Flux

Les champs Flux supplémentaires, les sauts de ligne et les commandes protégées
survivent à un aller-retour dans le CSV de projet commun.

### AC-609 — Reconstruction Flux toujours verrouillée

Même pour une version reconnue autorisant l'extraction, la capacité
`RECONSTRUCT` reste absente tant que l'import et l'écriture FPK ne sont pas validés.

### AC-610 — Validation d'import Flux indépendante

Pour une version reconnue, le validateur relance une extraction de contrôle et
exige le même ensemble d'occurrences, des identifiants uniques, des champs
structurels inchangés et le même ordre de commandes/balises. Il refuse les
projets mal rattachés, les versions inconnues, les chemins redirigés et toute
extraction de contrôle ambiguë. Le CSV et le FPK conservent exactement leurs
empreintes ; aucune copie ni archive reconstruite n'est créée.

### AC-611 — Plan d'import Flux en mémoire

Le plan est déterministe, ne crée aucun fichier et contient uniquement les
occurrences acceptées par AC-610. Il fige les empreintes du CSV/FPK, les chemins
structurels et le nombre exact de fragments des dialogues. Les clés Ruby et les
sources encore non prises en charge restent bloquées.

### AC-612 — Candidat Flux synthétique séparé

Le candidat est créé hors de l'original, réextrait avant publication et conserve
exactement l'inventaire source. Tout membre modifié hors plan, toute destination
concurrente ou tout échec de validation annule la publication sans écrasement.
Les remplacements sont relus à leur occurrence exacte et `RECONSTRUCT` reste
absente.

### AC-613 — Copie de travail et rollback Flux

Une copie complète est comparée à l'original avant le test. Le candidat est
installé atomiquement uniquement dans cette copie, puis une sauvegarde externe
restaure exactement son FPK. Les empreintes de toute la copie et de l'original
sont identiques aux références après rollback. Un échec injecté après
installation doit également aboutir à ce même état restauré.

## 9. Rapport communautaire

### AC-701 — Résumé Discord

Le rapport contient un bloc court copiable dans Discord.

### AC-702 — Mention obligatoire

Le résumé contient :

> Validation analytique. L'aventure complète n'a pas été jouée physiquement de bout en bout.

### AC-703 — Confidentialité

Le rapport public ne contient :

- aucun dialogue ;
- aucune traduction ;
- aucun nom d'utilisateur Windows ;
- aucun chemin personnel complet.

### AC-704 — Statut cohérent

- erreur bloquante → rouge ;
- limites importantes → jaune ;
- analyses supportées sans anomalie bloquante → vert analytique.

Le vert ne signifie jamais « jeu terminé par un testeur humain ».

## 10. Tests manuels Windows avant publication

1. Installer la v1.1 par-dessus la v1.0.2.
2. Vérifier qu'un seul produit apparaît dans les applications installées.
3. Ouvrir un ancien projet v1.0.2.
4. Vérifier que les traductions sont conservées.
5. Tester une fixture Essentials.
6. Tester une structure inconnue.
7. Tester le diagnostic public.
8. Tester la reconstruction sur une copie synthétique.
9. Désinstaller puis réinstaller.
10. Vérifier le Setup sur un second PC sans environnement Python, lorsque possible.

## 11. Porte de sortie

La Release Candidate v1.1 ne peut être publiée que si :

- tous les tests critiques AC-001, AC-004, AC-103, AC-104, AC-201, AC-401, AC-406, AC-504, AC-505 et AC-703 réussissent ;
- aucune régression bloquante de la v1.0.2 n'est connue ;
- la prise en charge Flux est marquée expérimentale ou absente si elle n'est pas suffisamment sûre ;
- les limites sont documentées dans les notes de version.
