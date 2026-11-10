# Pokémon Fangame Engine Compatibility Research

**Status:** Research baseline — 10 November 2026  
**Purpose:** Guide adapter priorities for Pokémon Fangame Translator (PFT).

## Executive summary

PFT must not assume that every Pokémon fangame uses Pokémon Essentials, RPG Maker XP, or Pokémon Flux. It should identify the underlying technology and the game framework/profile independently, then select a compatible read-only analyzer and (only when validated) extraction/rebuild adapters.

The strongest first target remains RPG Maker XP + Pokémon Essentials because it is historically well established, has substantial community documentation, and is already the project's tested baseline. This is a practical priority, not proof that it represents a measured majority of all current fangames.

Pokémon SDK (PSDK) is a separate important ecosystem. Current Pokémon Workshop documentation describes RPG Maker XP for events, Tiled for maps, and Pokémon Studio for game data. Pokémon Studio 3.0 has been announced with a new event system intended to move away from the RPG Maker XP dependency; PFT must distinguish PSDK generations and wait for released formats before claiming support.

Custom engines and platforms such as Pokéngine should be researched independently. Unity, Godot, RPG Maker MV/MZ, and other general-purpose engines are candidates for investigation, not supported targets until real Pokémon game samples and safe extraction paths are verified.

ROM hacks are technically separate from standalone fangames. They should not be included in the same engine-adapter claims; any future support needs a separate pipeline.

## Evidence and findings

### 1. RPG Maker XP + Pokémon Essentials
- Pokémon Essentials is a framework for RPG Maker XP, not a separate general-purpose engine.
- Community material describes Essentials as one of the best-known and widely used Pokémon fangame starter kits.
- PFT already has a tested Essentials-oriented workflow and a dedicated adapter.
- Variants and heavily modified projects may not share exact signatures or data layouts; version-aware detection is required.

**PFT priority:** P0 — strengthen the existing adapter, add version/profile detection, broaden fixture coverage, and distinguish stock Essentials from modified projects.

### 2. Pokémon SDK (PSDK) / Pokémon Workshop
- Current Pokémon Workshop documentation describes Pokémon Studio for game data, Tiled for maps, RPG Maker XP for event editing in the documented current workflow, and PSDK/LiteRGSS as the runtime.
- This is not equivalent to a normal Essentials project even though RPG Maker XP appears in both workflows.
- PSDK documentation describes its own text database and CSV translation-oriented facilities.
- Pokémon Workshop has announced Pokémon Studio 3.0 with a new event system intended to replace the remaining RPG Maker XP event dependency. PFT must verify actual released project formats before implementing support.

**PFT priority:** P1 — build a separate PSDK profile/adapter after collecting versioned sample projects and documenting text, map, event, and database formats.

### 3. RPG Maker XP without Pokémon Essentials
A plain RMXP project can share some container formats with Essentials while having entirely different scripts, event conventions, and text locations. Detect it as RPG Maker XP generic, not automatically as Essentials.

**PFT priority:** P1 — read-only project recognition and inventory first; extraction only for well-understood command/data formats; reconstruction later.

### 4. Pokémon Flux and custom game formats
PFT project notes describe Flux as requiring a separate workflow around a custom FPK archive. This demonstrates why custom projects need isolated adapters and exact version gates.

**PFT priority:** P1/P2 — keep the existing Flux adapter optional and experimental until supported versions, archive parsing, and rebuild invariants are fully tested. Flux must remain a separate adapter with no shared Essentials extraction or reconstruction path. The current default registry does instantiate the Flux adapter, so it is not yet an optional installable module; making adapter registration configurable is a separate future architecture task.

### 5. RPG Maker MV / MZ
MV and MZ are distinct generations with different project structures and data formats from RMXP. A similarly named experimental Pokémon Essentials project for MV is not evidence that the established Essentials project migrated to MV; each candidate must be checked independently.

**PFT priority:** P2 — research real Pokémon fangame samples, determine text storage and plugin effects, then implement read-only detection before extraction.

### 6. Pokéngine and other creator platforms
Pokéngine is a distinct platform/ecosystem and should not be classified as RPG Maker merely because it produces Pokémon-style games. Its project storage, text model, and export capabilities need independent research.

**PFT priority:** P2/P3 — investigate whether local project files or exports can be safely and repeatably translated.

### 7. Unity, Godot, and custom engines
These are general-purpose engines, not Pokémon-specific formats. Text may live in JSON, CSV, localization tables, databases, assets, bundles, or compiled resources. Engine identification alone is not enough to guarantee translation support.

**PFT priority:** P3 — generic read-only inventory and localization-file discovery first; specialized adapters only when real sample projects justify the effort.

### 8. ROM hacks
ROM hacks modify or patch an existing Pokémon ROM and use a different toolchain from standalone PC fangames. They are not a normal game-folder adapter target.

**PFT priority:** Out of scope for the first standalone-fangame roadmap. Revisit as a separately scoped pipeline.

## Recommended development order
1. Stabilize the adapter contract and registry; remove any implicit/default dependence on Flux.
2. Strengthen Essentials detection and extraction across known versions and variants.
3. Add a generic RPG Maker XP read-only profile so unknown XP games are not mislabelled Essentials.
4. Research and implement a separate PSDK profile, accounting for PSDK/Studio version differences.
5. Harden the experimental Flux adapter behind explicit confidence/version gates.
6. Investigate MV/MZ and Pokéngine with real project samples.
7. Add generic localization discovery for Unity/Godot/custom games, then specialized adapters justified by evidence.
8. Consider ROM hacks only as a separately scoped pipeline.

## Detection and safety rules
- Separate engine detection from game-framework/profile detection.
- Return evidence for every match: file signatures, metadata, version markers, and confidence.
- Allow multiple candidate profiles; do not force a match when evidence is ambiguous.
- Unknown or unsupported projects remain read-only.
- Recognizing an engine family does not automatically authorize extraction or reconstruction.
- Every adapter declares supported versions, readable formats, writable formats, and known limitations.
- Reconstruction is enabled only after a successful dry run and a backup/copy plan.
- Never modify the selected original game folder.
- Never upload user fangame files, proprietary assets, or game data to public repositories. Use synthetic/minimal fixtures or legally redistributable samples.

## Sources
1. Pokémon Workshop Docs — Getting Started: https://docs.pokemonworkshop.com/getting-started/
2. Pokémon Workshop Docs — RPG Maker XP basics: https://docs.pokemonworkshop.com/rpg-maker-xp/rpg-maker-xp-basics/
3. Pokémon SDK documentation — README: https://psdk.pokemonworkshop.com/yard/index.html
4. Pokémon Workshop — Pokémon Studio 3.0 event-system announcement: https://pokemonworkshop.com/en/articles/pokemon-studio-v3-event-system-rmxp-replacement/
5. PokéCommunity Daily — A guide to beginning Pokémon fangame development in RMXP: https://daily.pokecommunity.com/2017/07/05/a-guide-to-beginning-pokemon-fangame-development-in-rmxp/
6. Nuzlocke France — Interview with the creator of Pokémon Sacred Phoenix: https://nuzlocke-france.fr/interview-damras-anarion-createur-de-pokemon-sacred-phoenix/
7. Pokéngine — Pokémon Essentials vs Pokéngine discussion: https://pokengine.org/forums?thread=113



## Independence, copyright, and clean-room implementation

PFT is distributed under GPL-3.0-or-later (see the repository LICENSE). This license governs PFT's own code; it does not grant permission to copy Pokémon Flux's game code, assets, dialogue, executable, or other copyrighted game content.

The Flux adapter is an independently authored parser/detector in PFT. Its current use of a specific release's file hashes and expected archive-member names is a compatibility fingerprint, not a redistribution of the game. Keep it as an optional research/compatibility case, not as the architectural model for every engine.

For future adapters:
- Implement readers from documented/public formats or independently observed file structures; do not copy another project's source code unless its license explicitly permits the intended use and all license obligations can be met.
- Do not include game executables, archives, artwork, music, maps, dialogue, or extracted user data in source control, releases, test fixtures, or CI artifacts.
- Use synthetic fixtures created by PFT contributors, or samples that are explicitly licensed for redistribution.
- Record provenance for format documentation and test samples.
- Keep each game-specific adapter small and replaceable; shared application services must not depend on a particular fangame's internal design.
- When a format cannot be understood without unauthorized code or assets, stop and mark it unsupported rather than reverse-engineering protected content or bypassing access controls.

This is an engineering hygiene policy, not legal advice. Check individual third-party licenses and obtain qualified advice for distribution questions.

## Open research questions
- Which Essentials versions and community forks are present in the user's real-world test library?
- Which PSDK project versions can be obtained as redistributable test fixtures?
- What exact PSDK text database formats are emitted by each supported version?
- What signatures distinguish plain RMXP, Essentials, PSDK, and heavily modified hybrids?
- Can MV/MZ Pokémon projects be identified from reliable samples?
- Which Pokéngine project exports are accessible offline and contain editable text?
- Which Unity/Godot games have localization assets that can be safely processed without unpacking protected bundles?