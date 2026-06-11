import sys
from pathlib import Path

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"
