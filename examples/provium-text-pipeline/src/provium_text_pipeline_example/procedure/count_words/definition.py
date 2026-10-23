from provium import ProcedureDefinition

_path = "provium_text_pipeline_example.procedure.count_words"

COUNT_WORDS = ProcedureDefinition(
    identifier="provium_text_pipeline_example.CountWordsV1",
    label="Count words",
    target=f"{_path}.implementation:CountWordsV1Procedure",
    contract=f"{_path}.contract:CountWordsV1ProcedureContract",
    description="Count the tokens in a document",
)
