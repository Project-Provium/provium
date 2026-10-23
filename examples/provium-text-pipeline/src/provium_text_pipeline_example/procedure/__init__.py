from provium import ProcedureCatalog

from .count_words.definition import COUNT_WORDS
from .tokenize.definition import TOKENIZE

catalog = ProcedureCatalog()
catalog.register(TOKENIZE)
catalog.register(COUNT_WORDS)

__all__ = ["COUNT_WORDS", "TOKENIZE", "catalog"]
