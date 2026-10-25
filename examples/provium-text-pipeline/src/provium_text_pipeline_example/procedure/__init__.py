from provium import ProcedureCatalog

from provium_text_pipeline_example.procedure.count_words.definition import COUNT_WORDS
from provium_text_pipeline_example.procedure.tokenize.definition import TOKENIZE

catalog = ProcedureCatalog()
catalog.register(TOKENIZE)
catalog.register(COUNT_WORDS)

__all__ = ["COUNT_WORDS", "TOKENIZE", "catalog"]
