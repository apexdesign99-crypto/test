"""AI社員 — 職務定義書 1 枚で立ち上がる、記憶を持つ業務エージェント。"""

from .agent import Employee, Listener, TurnResult
from .profile import EmployeeProfile, build_profile
from .workspace import Workspace, roster

__all__ = [
    "Employee",
    "EmployeeProfile",
    "Listener",
    "TurnResult",
    "Workspace",
    "build_profile",
    "roster",
]
__version__ = "0.1.0"
