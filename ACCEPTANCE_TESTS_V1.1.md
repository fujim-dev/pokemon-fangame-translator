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
