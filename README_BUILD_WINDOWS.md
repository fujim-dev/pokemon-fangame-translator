# Construire l'installateur Windows v1.0.2

Pour les mainteneurs uniquement. Les utilisateurs ordinaires téléchargent directement le `Setup.exe`.

1. Installer Python 3.10 à 3.13 64 bits avec Tkinter.
2. Installer Inno Setup 6.
3. Lancer `CREER_INSTALLATEUR_WINDOWS.bat`.
4. Récupérer dans `release` :
   - `Pokemon_Fangame_Translator_Setup_v1.0.2.exe`
   - `SHA256.txt`
   - `RELEASE_NOTES_V1.0.2.md`
   - `LICENSE`

Le script installe les dépendances de build localement, génère la liste des composants tiers, compile l'application puis produit l'installateur.
