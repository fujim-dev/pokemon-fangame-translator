# SPDX-License-Identifier: GPL-3.0-or-later
"""Empêche les anciennes écritures CSV de contourner une provenance Essentials."""
from __future__ import annotations

import json
import os
from pathlib import Path

from project_identity import PROJECT_METADATA_NAME
from safe_io import read_stable_file

from .models import RepairError


def assert_legacy_direct_write_allowed(csv_path: Path) -> None:
    """Réserve les projets Essentials rattachés au service transactionnel.

    Les fonctions historiques restent disponibles pour les CSV autonomes. Dès
    qu'une identité de projet voisine annonce Essentials, même sans manifeste
    récent, une écriture directe pourrait fabriquer une cohérence apparente :
    elle est donc refusée et une nouvelle extraction est demandée si nécessaire.
    """
    identity_path = csv_path.expanduser().resolve().parent / PROJECT_METADATA_NAME
    if not os.path.lexists(identity_path):
        return
    try:
        payload, _state = read_stable_file(identity_path)
        identity = json.loads(payload.decode("utf-8-sig"))
    except Exception as exc:
        raise RepairError(
            "L'identité voisine est illisible : l'écriture directe du CSV est refusée."
        ) from exc
    if not isinstance(identity, dict):
        raise RepairError(
            "L'identité voisine est ambiguë : l'écriture directe du CSV est refusée."
        )
    adapter_id = str(identity.get("adapter_id") or "")
    if not adapter_id:
        raise RepairError(
            "L'identité voisine ne précise aucun adaptateur : l'écriture directe est refusée."
        )
    if adapter_id == "pokemon_essentials":
        raise RepairError(
            "Ce CSV appartient à un projet Pokémon Essentials. Sa réparation ou sa "
            "restauration doit passer par le service transactionnel du Studio ; un "
            "ancien projet doit d'abord être réextrait pour obtenir une provenance fiable."
        )
