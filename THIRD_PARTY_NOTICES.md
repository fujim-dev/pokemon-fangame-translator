# Composants tiers

La distribution Windows peut intégrer des composants tiers, chacun restant soumis à sa propre licence. La liste exacte dépend de l'environnement de compilation et est générée dans `THIRD_PARTY_PACKAGES.txt` pendant le build.

Composants principaux utilisés directement :

- **Argos Translate** — moteur de traduction hors ligne.
- **CTranslate2** — exécution optimisée des modèles de traduction.
- **SentencePiece**, **Stanza** et **Sacremoses** — traitement linguistique.
- **PyInstaller** — création de l'application Windows autonome.
- **Inno Setup** — création de l'installateur Windows.

Les avis et fichiers de licence détectés dans les paquets Python sont copiés dans `THIRD_PARTY_LICENSES` pendant la compilation. La licence GPL du présent projet ne remplace pas les licences des composants tiers.
