"""Local-only GPU runtime adapters.

This package deliberately has no heavyweight imports at module import time. The production
container supplies the pinned GPU dependencies, while dependency-light tests inject fakes.
"""

