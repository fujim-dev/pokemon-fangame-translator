from .engine import apply_repair_plan, build_repair_candidate
from .models import (
    RepairAction,
    RepairError,
    RepairPlan,
    RepairResult,
    RestorationResult,
)
from .planner import plan_csv_repairs, plan_csv_repairs_payload, save_repair_plan
from .rollback import restore_csv_backup
from .transactional import (
    apply_repair_plan_transactional,
    restore_csv_backup_transactional,
)
from .safe_fixes import (
    PROTECTED_RE,
    extract_protected,
    protected_command_diff,
    restore_simple_commands,
    split_protected,
)

__all__ = [
    "PROTECTED_RE",
    "RepairAction",
    "RepairError",
    "RepairPlan",
    "RepairResult",
    "RestorationResult",
    "apply_repair_plan",
    "apply_repair_plan_transactional",
    "build_repair_candidate",
    "extract_protected",
    "plan_csv_repairs",
    "plan_csv_repairs_payload",
    "protected_command_diff",
    "restore_csv_backup",
    "restore_csv_backup_transactional",
    "restore_simple_commands",
    "save_repair_plan",
    "split_protected",
]
