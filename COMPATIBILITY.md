# Compatibilité

Pokémon Fangame Translator (PFT) est une bêta publique. La compatibilité dépend du moteur, de la version exacte du fangame, des modifications apportées par ses créateurs et de l'emplacement réel des textes.

**Règle de lecture :** reconnaître un moteur ou inventorier ses fichiers ne signifie pas que l'extraction, la traduction ou la reconstruction sont prises en charge.

## État actuel du logiciel (v1.0.2)

| Famille / profil | Détection | Analyse | Extraction | Reconstruction |
|---|---|---|---|---|
| Pokémon Essentials / RPG Maker XP compatible | Implémentée | Implémentée selon les formats reconnus | Implémentée selon les formats reconnus | Implémentée sur les formats validés |
| Pokémon Flux Episode 2 v2.1.0 — signature exacte connue | Adaptateur expérimental | Analyse statique | Extraction des occurrences vérifiables vers le CSV commun | **Désactivée** |
| Pokémon Flux — autre version ou signature inconnue | Partielle / non reconnue | Inventaire prudent si possible | Bloquée | Bloquée |
| RPG Maker XP générique sans Essentials | Pas de profil dédié garanti | Pas de prise en charge dédiée | Bloquée | Bloquée |
| Pokémon SDK (PSDK) | Non pris en charge | Non pris en charge | Bloquée | Bloquée |
| RPG Maker MV / MZ | Non pris en charge | Non pris en charge | Bloquée | Bloquée |
| Pokéngine | Non pris en charge | Non pris en charge | Bloquée | Bloquée |
| Unity / Godot / moteurs personnalisés | Non pris en charge | Non pris en charge | Bloquée | Bloquée |
| ROM hacks | Hors périmètre actuel | Hors périmètre actuel | Hors périmètre actuel | Hors périmètre actuel |

## Profil actuellement le mieux validé

- **Pokémon Myth 2** : analyse, extraction, traduction, reconstruction et lancement d'une copie française ont été testés. L'intégralité de l'aventure n'est pas garantie.
- **Pokémon Essentials v21.1** : le dépôt contient des tests automatisés et des validations ciblées pour plusieurs structures Essentials. Cela ne vaut pas validation de tous les fangames basés sur cette version.

## Détails importants

### Pokémon Essentials et RPG Maker XP

Pokémon Essentials est un framework construit sur RPG Maker XP. Un projet XP générique ne doit donc pas être automatiquement présenté comme un projet Essentials.

La prise en charge Essentials concerne les structures et formats réellement reconnus par l'adaptateur. Les projets fortement modifiés, les plugins particuliers et les textes stockés dans des scripts peuvent rester hors couverture.

### Pokémon Flux

L'adaptateur Flux est séparé du chemin Essentials et expérimental. La version Flux Episode 2 v2.1.0 ne peut être identifiée comme version connue qu'avec les signatures attendues. Pour cette signature précise, l'analyse statique et l'extraction des occurrences vérifiables vers le CSV commun sont autorisées. L'import dans le FPK et la reconstruction Flux restent désactivés tant que le chemin d'écriture n'a pas été validé de bout en bout sur une copie de travail.

Ne renommez pas un dossier pour tenter de forcer la détection : le nom du dossier n'est pas une preuve de compatibilité.

### Pokémon SDK (PSDK)

PSDK doit être traité comme un profil distinct d'Essentials. Le fait que certaines versions de PSDK utilisent RPG Maker XP pour les événements ne rend pas leurs données, cartes ou méthodes de traduction interchangeables avec celles d'Essentials. Aucune prise en charge PSDK n'est actuellement revendiquée.

## Priorités de recherche et de développement

1. Stabiliser et tester les contrats d'adaptateurs et la détection existante.
2. Consolider la couverture Essentials et documenter les versions/profils réellement validés.
3. Ajouter un profil RPG Maker XP générique strictement en lecture seule avant toute extraction.
4. Étudier PSDK par version et par format, à partir d'échantillons légitimes et de fixtures synthétiques.
5. Garder Flux isolé et expérimental jusqu'à validation de son chemin d'écriture.
6. Étudier ensuite MV/MZ, Pokéngine, puis les projets Unity/Godot/custom lorsqu'un cas réel justifie un adaptateur.

## Limites et sécurité

- Archives chiffrées ou formats personnalisés non documentés.
- Textes intégrés aux scripts ou générés dynamiquement.
- Moteurs fortement modifiés et plugins inconnus.
- Commandes de contrôle inhabituelles.
- Mises à jour d'un fangame pouvant modifier sa compatibilité.
- L'analyse statique ne prouve pas que tous les dialogues ont été découverts ni que toute l'aventure fonctionne.

PFT ne doit jamais exécuter les scripts inconnus d'un fangame pour le détecter ou l'analyser. Une détection ambiguë ou une version non reconnue doit rester en lecture seule. La reconstruction doit toujours créer une copie séparée et préserver l'original.

Une analyse réussie ne garantit pas que toute l'aventure fonctionnera. Testez toujours une copie séparée et signalez le résultat avec la version exacte du jeu.
