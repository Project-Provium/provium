from provium import ProcedureContract, ProcedureInputs, ProcedureOutputs
from provium import input as input_field
from provium import output as output_field

from provium_text_pipeline_example.artifact import TOKENS, WORD_COUNT


class CountWordsV1ProcedureContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        pass

    class Inputs(ProcedureInputs):
        tokens = input_field(TOKENS)

    class Outputs(ProcedureOutputs):
        word_count = output_field(WORD_COUNT)
