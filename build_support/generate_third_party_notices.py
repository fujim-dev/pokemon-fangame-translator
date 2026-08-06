from __future__ import annotations

from importlib import metadata
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parent.parent
out = ROOT / "THIRD_PARTY_PACKAGES.txt"
licenses_dir = ROOT / "THIRD_PARTY_LICENSES"
if licenses_dir.exists():
    shutil.rmtree(licenses_dir)
licenses_dir.mkdir(parents=True)

rows = []
packages_dir = ROOT / ".build_packages"
distributions = metadata.distributions(path=[str(packages_dir)]) if packages_dir.exists() else []
for dist in sorted(distributions, key=lambda d: (d.metadata.get("Name") or "").casefold()):
    name = dist.metadata.get("Name") or "Inconnu"
    version = dist.version
    license_name = (dist.metadata.get("License") or "Non indiquée").strip().replace("\n", " ")
    homepage = dist.metadata.get("Home-page") or ""
    rows.append(f"{name} {version} | Licence: {license_name} | {homepage}")

    copied = 0
    for file in dist.files or []:
        filename = Path(str(file)).name
        if not re.match(r"^(LICENSE|LICENCE|COPYING|NOTICE)(\..*)?$", filename, re.IGNORECASE):
            continue
        source = Path(dist.locate_file(file))
        if not source.is_file():
            continue
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        target = licenses_dir / f"{safe}_{version}_{copied}_{filename}"
        try:
            shutil.copy2(source, target)
            copied += 1
        except OSError:
            pass

out.write_text(
    "PAQUETS TIERS DÉTECTÉS PENDANT LE BUILD\n" + "=" * 72 + "\n" + "\n".join(rows),
    encoding="utf-8",
)
print(f"Avis tiers générés : {len(rows)} paquet(s), {len(list(licenses_dir.iterdir()))} fichier(s) de licence.")
