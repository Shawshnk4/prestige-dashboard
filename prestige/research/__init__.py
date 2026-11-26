"""Research modules of the PRESTIGE model, copied unchanged from the research code base except for one hook:
v2_core.Ctx accepts its data panels as an argument. They import each other by bare name, so this folder is put on
sys.path before they are imported."""
import os, sys
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)
import v2_core  # noqa: E402
import engine  # noqa: E402
import sectors  # noqa: E402
