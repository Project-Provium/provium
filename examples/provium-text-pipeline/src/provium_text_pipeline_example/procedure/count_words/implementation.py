from provium import Procedure

from .contract import CountWordsV1ProcedureContract
from .definition import COUNT_WORDS


class CountWordsV1Procedure(
    Procedure[
        None,
        CountWordsV1ProcedureContract.SetupInputs,
        CountWordsV1ProcedureContract.Inputs,
        CountWordsV1ProcedureContract.Outputs,
    ]
):
    definition = COUNT_WORDS

    def process(self, context, configuration, inputs, outputs):
        with inputs.tokens.open() as reader:
            count = len(reader.read())
        with outputs.word_count.open() as writer:
            writer.write(count)
