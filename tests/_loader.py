"""Load integration modules without importing Home Assistant."""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

PACKAGE_NAME = "_waha_whatsapp_test"
INTEGRATION_PATH = Path(__file__).parents[1] / "custom_components" / "waha_whatsapp"


def load_integration_module(name: str) -> ModuleType:
    """Load one module with working relative imports."""
    if PACKAGE_NAME not in sys.modules:
        package = ModuleType(PACKAGE_NAME)
        package.__path__ = [str(INTEGRATION_PATH)]
        sys.modules[PACKAGE_NAME] = package

    full_name = f"{PACKAGE_NAME}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    spec = spec_from_file_location(full_name, INTEGRATION_PATH / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load integration module {name}")
    module = module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module
