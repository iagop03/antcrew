"""AntCrew Enterprise Edition — optional features loaded at startup.

This package is absent in the open-source distribution. The platform
core (app/) imports nothing from here directly; all integration points
go through app/ee_hooks.py so the app starts cleanly without this package.

To activate EE features, ensure this package is installed and the
ANTCREW_EE=1 environment variable is set.
"""
