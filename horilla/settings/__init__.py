"""
horilla/settings/__init__.py
Combines official base settings + client overrides.
"""

import os

# Importing ``horilla.settings.careerhub`` first initializes this package. Do
# not execute Horilla's private settings (or their production/database checks)
# for that deliberately isolated public runtime.
if os.environ.get("DJANGO_SETTINGS_MODULE") != "horilla.settings.careerhub":
    from .base import *

    # Optional upstream integrations, then required versioned TVG configuration.
    from .addons import *
    from .tvg import *

    # Missing local overrides are expected in Docker; dependency/import errors
    # inside an existing overrides file must not silently disable security.
    try:
        from .local_settings import *
    except ModuleNotFoundError as exc:
        if exc.name != "horilla.settings.local_settings":
            raise
