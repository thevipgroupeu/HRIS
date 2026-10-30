"""Optional machine-local overrides, intentionally excluded from Docker images.

THEVIPGROUP application settings live in the shipped ``tvg.py`` module. Keep
this file optional and never put required app registration or secrets in it.
Use environment variables for production configuration. Do not import all of
base.py here: that would overwrite storage configuration applied by addons.py.
"""
