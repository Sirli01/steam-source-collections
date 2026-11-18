"""Group a Steam library by how each game was acquired.

The public Steam Web API tells you *what* you own, but not *how* you got it.
This package reads the account license page, aligns package names to appids
and produces a dry-run collection plan. No credentials are stored or sent
anywhere except to Steam's own API when explicitly requested.
"""

__version__ = "0.7.3"
__all__ = ["__version__"]
