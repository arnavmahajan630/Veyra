"""ClickHouse lineage index: schema and migrations, row builders, and the query library.

Everything the APIs need to read the index lives in :mod:`veyra_lineage.queries`; handlers call
those and never write SQL themselves.
"""

__version__ = "0.1.0"
