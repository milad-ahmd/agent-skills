from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import run  # noqa: F401  (type-only)


def build():
    from .models import Model  # deferred: this cycle never runs at import time
    return Model()
