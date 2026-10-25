import json
import pickle
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace, new_class
from typing import Any

import pytest

from provium import (
    Artifact,
    ArtifactDefinition,
    ArtifactReader,
    ArtifactWriter,
    Procedure,
    ProcedureConfig,
    ProcedureContract,
    ProcedureContractMetadata,
    ProcedureDefinition,
    ProcedureInputs,
    ProcedureIOFieldMetadata,
    ProcedureOutputs,
    ProcedureProcessContext,
    input,
    optional_input,
    optional_output,
    output,
    repeated_input,
)


class Config(ProcedureConfig):
    pass


class OtherConfig(ProcedureConfig):
    pass


class RequiredConfig(ProcedureConfig):
    value: int


class Reader(ArtifactReader):
    pass


class Writer(ArtifactWriter):
    pass


class ExampleArtifact(Artifact[Reader, Writer]):
    definition = ArtifactDefinition(
        "example.ExampleArtifactV1",
        "example.artifacts:ExampleArtifact",
        "An example artifact.",
    )
    reader = Reader
    writer = Writer


EXAMPLE_ARTIFACT: ArtifactDefinition[ExampleArtifact] = ExampleArtifact.definition


class SetupInputs(ProcedureInputs):
    pass


class Inputs(ProcedureInputs):
    pass


class Outputs(ProcedureOutputs):
    pass


class Contract(ProcedureContract[Config]):
    configuration = Config
    SetupInputs = SetupInputs
    Inputs = Inputs
    Outputs = Outputs


EXAMPLE_DEFINITION = ProcedureDefinition(
    identifier="example.ExampleV1",
    target="example.procedures:ExampleProcedure",
    label="Example",
    description="An example procedure.",
    contract=Contract,
)


class ExampleProcedure(Procedure[Config, SetupInputs, Inputs, Outputs]):
    definition = EXAMPLE_DEFINITION

    def process(
        self,
        context: ProcedureProcessContext,
        configuration: Config,
        inputs: Inputs,
        outputs: Outputs,
    ) -> None:
        pass


def test_procedure_is_a_specialized_class_with_a_definition() -> None:
    assert issubclass(ExampleProcedure, Procedure)
    assert ExampleProcedure.definition is EXAMPLE_DEFINITION


def test_procedure_definition_exposes_lightweight_metadata() -> None:
    assert EXAMPLE_DEFINITION.identifier == "example.ExampleV1"
    assert EXAMPLE_DEFINITION.target == "example.procedures:ExampleProcedure"
    assert EXAMPLE_DEFINITION.label == "Example"
    assert EXAMPLE_DEFINITION.description == "An example procedure."
    assert EXAMPLE_DEFINITION.contract is Contract


def test_procedure_definition_accepts_a_lazy_contract_reference() -> None:
    definition = ProcedureDefinition(
        "example.LazyContractV1",
        "example.procedures:LazyProcedure",
        "Lazy",
        None,
        "example.contracts:Contract",
    )

    assert definition.contract == "example.contracts:Contract"


def test_procedure_definition_resolves_and_caches_a_lazy_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imports: list[str] = []

    def import_module(name: str) -> object:
        imports.append(name)
        return SimpleNamespace(nested=SimpleNamespace(Contract=Contract))

    monkeypatch.setattr("provium.procedure.definition.import_module", import_module)
    definition = ProcedureDefinition(
        "example.LazyContractV1",
        "example.procedures:LazyProcedure",
        "Lazy",
        None,
        "example.contracts:nested.Contract",
    )

    assert imports == []
    assert definition.resolve_contract() is Contract
    assert definition.resolve_contract() is Contract
    assert imports == ["example.contracts"]


@pytest.mark.parametrize(
    "contract",
    [
        "example.contracts",
        ":Contract",
        "example.contracts:",
        "example..contracts:Contract",
        "example.contracts:Contract:Nested",
    ],
)
def test_procedure_definition_rejects_malformed_contract_references(
    contract: str,
) -> None:
    with pytest.raises(ValueError, match="contract.*module:attribute"):
        ProcedureDefinition(
            "example.LazyContractV1",
            "example.procedures:LazyProcedure",
            "Lazy",
            None,
            contract,
        )


def test_lazy_contract_reference_must_resolve_to_a_compiled_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(Contract=object),
    )
    definition = ProcedureDefinition(
        "example.LazyContractV1",
        "example.procedures:LazyProcedure",
        "Lazy",
        None,
        "example.contracts:Contract",
    )

    with pytest.raises(TypeError, match="concrete compiled ProcedureContract"):
        definition.resolve_contract()


def test_procedure_definition_can_cross_process_serialization_boundaries() -> None:
    restored = pickle.loads(pickle.dumps(EXAMPLE_DEFINITION))

    assert restored == EXAMPLE_DEFINITION
    assert restored.contract is Contract


def test_lazy_contract_reference_survives_serialization_without_resolution() -> None:
    definition = ProcedureDefinition(
        "example.LazyContractV1",
        "example.procedures:LazyProcedure",
        "Lazy",
        None,
        "example.contracts:Contract",
    )

    restored = pickle.loads(pickle.dumps(definition))

    assert restored == definition
    assert restored.contract == "example.contracts:Contract"
    assert restored._resolved_contract is None


def test_procedure_definition_builds_an_ordered_invocation_synopsis() -> None:
    class SynopsisContract(ProcedureContract[Config]):
        configuration = Config

        class SetupInputs(ProcedureInputs):
            model = input(EXAMPLE_ARTIFACT)

        class Inputs(ProcedureInputs):
            image = input(EXAMPLE_ARTIFACT)
            images = repeated_input(EXAMPLE_ARTIFACT, minimum=1, maximum=4)

        class Outputs(ProcedureOutputs):
            result = optional_output(EXAMPLE_ARTIFACT)

    definition = ProcedureDefinition(
        "example.SynopsisV1",
        "example.procedures:SynopsisProcedure",
        "Synopsis",
        None,
        SynopsisContract,
    )

    assert definition.invocation_synopsis == (
        "provium execute example.SynopsisV1 \\\n"
        "  --setup-input model=PATH \\\n"
        "  --input image=PATH \\\n"
        "  --input images=PATH ... (1..4 bindings) \\\n"
        "  [--output result=PATH] \\\n"
        "  [--procedure-config FILE ...]"
    )


def test_unconfigured_empty_contract_has_a_minimal_invocation_synopsis() -> None:
    class EmptyContract(ProcedureContract[None]):
        configuration = None

    definition = ProcedureDefinition(
        "example.EmptyV1",
        "example.procedures:EmptyProcedure",
        "Empty",
        None,
        EmptyContract,
    )

    assert definition.invocation_synopsis == "provium execute example.EmptyV1"


def test_invocation_synopsis_quotes_the_procedure_identifier() -> None:
    class EmptyContract(ProcedureContract[None]):
        configuration = None

    definition = ProcedureDefinition(
        "example procedure; echo unsafe",
        "example.procedures:ExampleProcedure",
        "Example",
        None,
        EmptyContract,
    )

    assert definition.invocation_synopsis == (
        "provium execute 'example procedure; echo unsafe'"
    )


def test_optional_and_unbounded_inputs_are_shown_in_the_synopsis() -> None:
    class SynopsisContract(ProcedureContract[None]):
        configuration = None

        class SetupInputs(ProcedureInputs):
            model = optional_input(EXAMPLE_ARTIFACT)

        class Inputs(ProcedureInputs):
            images = repeated_input(EXAMPLE_ARTIFACT)

        class Outputs(ProcedureOutputs):
            result = output(EXAMPLE_ARTIFACT)

    definition = ProcedureDefinition(
        "example.OptionalV1",
        "example.procedures:OptionalProcedure",
        "Optional",
        None,
        SynopsisContract,
    )

    assert definition.invocation_synopsis == (
        "provium execute example.OptionalV1 \\\n"
        "  [--setup-input model=PATH] \\\n"
        "  [--input images=PATH ...] (0..unbounded bindings) \\\n"
        "  --output result=PATH"
    )


def test_single_binding_repeated_input_does_not_show_an_ellipsis() -> None:
    class SynopsisContract(ProcedureContract[None]):
        configuration = None

        class Inputs(ProcedureInputs):
            value = repeated_input(EXAMPLE_ARTIFACT, minimum=1, maximum=1)

    definition = ProcedureDefinition(
        "example.SingleV1",
        "example.procedures:SingleProcedure",
        "Single",
        None,
        SynopsisContract,
    )

    assert definition.invocation_synopsis == (
        "provium execute example.SingleV1 \\\n  --input value=PATH (1..1 bindings)"
    )


def test_procedure_definition_description_is_optional() -> None:
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:ExampleProcedure",
        "Example",
        None,
        Contract,
    )

    assert definition.description is None


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    [
        ("configuration", object, "ProcedureConfig class or None"),
        ("configuration", Config(), "ProcedureConfig class or None"),
        ("SetupInputs", Outputs, "ProcedureInputs class"),
        ("Inputs", Outputs, "ProcedureInputs class"),
        ("Outputs", Inputs, "ProcedureOutputs class"),
    ],
)
def test_procedure_contract_validates_runtime_declarations(
    attribute: str, value: object, message: str
) -> None:
    with pytest.raises(TypeError, match=message):
        type(
            "InvalidContract",
            (ProcedureContract,),
            {attribute: value},
        )


@pytest.mark.parametrize("attribute", ["SetupInputs", "Inputs", "Outputs"])
def test_procedure_contract_io_declarations_can_be_none(attribute: str) -> None:
    declarations: dict[str, object] = {
        "SetupInputs": SetupInputs,
        "Inputs": Inputs,
        "Outputs": Outputs,
    }
    declarations[attribute] = None

    contract = new_class(
        "NullableIOContract",
        (ProcedureContract[None],),
        exec_body=lambda namespace: namespace.update(declarations),
    )

    assert getattr(contract, attribute) is None


def test_procedure_contract_requires_at_least_one_io_declaration() -> None:
    with pytest.raises(
        TypeError,
        match="at least one setup input, processing input, or output",
    ):

        class EmptyContract(ProcedureContract[None]):
            SetupInputs = None
            Inputs = None
            Outputs = None


def test_procedure_contract_cannot_override_compilation_hooks() -> None:
    with pytest.raises(TypeError, match="cannot override contract compilation"):

        class InvalidContract(ProcedureContract[Config]):
            configuration = Config
            Outputs = Inputs  # type: ignore[assignment]

            @classmethod
            def _assert_io_declarations_are_valid(cls) -> None:
                pass


def test_procedure_contract_configuration_matches_generic_specialization() -> None:
    with pytest.raises(TypeError, match="configuration does not match"):

        class InvalidContract(ProcedureContract[Config]):
            configuration = OtherConfig


def test_unconfigured_procedure_contract_uses_none_specialization() -> None:
    class NoConfigContract(ProcedureContract[None]):
        configuration = None

    assert NoConfigContract.configuration is None


def test_contract_compiles_immutable_configuration_metadata() -> None:
    assert isinstance(Contract.metadata, ProcedureContractMetadata)
    assert Contract.metadata.configuration_target == (
        f"{Config.__module__}:{Config.__qualname__}"
    )
    assert Contract.metadata.configuration_schema == Config.model_json_schema()
    assert len(Contract.metadata.configuration_schema_digest or "") == 64
    assert len(Contract.metadata.digest) == 64

    schema = Contract.metadata.configuration_schema
    assert schema is not None
    schema["title"] = "Changed"
    assert Contract.metadata.configuration_schema == Config.model_json_schema()


def test_compiled_configuration_schema_is_json_serializable() -> None:
    assert json.loads(json.dumps(Contract.metadata.configuration_schema)) == (
        Config.model_json_schema()
    )


def test_contract_freezes_nested_configuration_schema_collections() -> None:
    class RequiredContract(ProcedureContract[RequiredConfig]):
        configuration = RequiredConfig

    assert RequiredContract.metadata.configuration_schema is not None
    assert RequiredContract.metadata.configuration_schema["required"] == ["value"]


def test_unconfigured_contract_compiles_absent_configuration_metadata() -> None:
    class NoConfigContract(ProcedureContract[None]):
        configuration = None

    assert NoConfigContract.metadata.configuration_target is None
    assert NoConfigContract.metadata.configuration_schema is None
    assert NoConfigContract.metadata.configuration_schema_digest is None


def test_contract_compiles_ordered_io_field_metadata_without_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ArtifactDefinition,
        "resolve",
        lambda self: pytest.fail("contract metadata must not resolve artifacts"),
    )

    class MetadataContract(ProcedureContract[Config]):
        configuration = Config

        class SetupInputs(ProcedureInputs):
            model = input(EXAMPLE_ARTIFACT, description="The setup model.")

        class Inputs(ProcedureInputs):
            first = input(EXAMPLE_ARTIFACT)
            values = repeated_input(EXAMPLE_ARTIFACT, minimum=1, maximum=4)

        class Outputs(ProcedureOutputs):
            preview = optional_output(EXAMPLE_ARTIFACT)

    assert MetadataContract.metadata.setup_inputs == (
        ProcedureIOFieldMetadata(
            name="model",
            artifact_identifier="example.ExampleArtifactV1",
            artifact_description="An example artifact.",
            direction="input",
            minimum=1,
            maximum=1,
            repeated=False,
            description="The setup model.",
        ),
    )
    assert [field.name for field in MetadataContract.metadata.inputs] == [
        "first",
        "values",
    ]
    assert MetadataContract.metadata.inputs[1].minimum == 1
    assert MetadataContract.metadata.inputs[1].maximum == 4
    assert MetadataContract.metadata.inputs[1].repeated is True
    assert MetadataContract.metadata.outputs[0].required is False


def test_equivalent_contract_inheritance_preserves_the_digest() -> None:
    class EquivalentContract(Contract):
        pass

    assert EquivalentContract.metadata == Contract.metadata


def test_contract_digest_changes_with_field_metadata() -> None:
    class FirstContract(ProcedureContract[Config]):
        configuration = Config

        class Inputs(ProcedureInputs):
            value = input(EXAMPLE_ARTIFACT, description="First description.")

    class SecondContract(ProcedureContract[Config]):
        configuration = Config

        class Inputs(ProcedureInputs):
            value = input(EXAMPLE_ARTIFACT, description="Second description.")

    assert FirstContract.metadata.digest != SecondContract.metadata.digest


def test_procedure_contract_requires_a_concrete_generic_specialization() -> None:
    with pytest.raises(TypeError, match="exactly one generic specialization"):

        class UnspecializedContract[ConfigT: ProcedureConfig](
            ProcedureContract[ConfigT]
        ):
            configuration = Config


@pytest.mark.parametrize("field", ["identifier", "target", "label"])
def test_procedure_definition_requires_nonempty_text(field: str) -> None:
    values: dict[str, object] = {
        "identifier": "example.ExampleV1",
        "target": "example.procedures:ExampleProcedure",
        "label": "Example",
        "description": None,
        "contract": Contract,
    }
    values[field] = ""

    with pytest.raises(ValueError, match=field):
        ProcedureDefinition(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["identifier", "target", "label", "description"])
def test_procedure_definition_requires_text_values(field: str) -> None:
    values: dict[str, object] = {
        "identifier": "example.ExampleV1",
        "target": "example.procedures:ExampleProcedure",
        "label": "Example",
        "description": "An example procedure.",
        "contract": Contract,
    }
    values[field] = object()

    with pytest.raises(TypeError, match=field):
        ProcedureDefinition(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("contract", [object, Contract()])
def test_procedure_definition_requires_a_contract_class(contract: object) -> None:
    with pytest.raises(TypeError, match="ProcedureContract"):
        ProcedureDefinition(
            "example.ExampleV1",
            "example.procedures:ExampleProcedure",
            "Example",
            None,
            contract,  # type: ignore[arg-type]
        )


def test_procedure_definition_requires_compiled_contract_metadata() -> None:
    with pytest.raises(TypeError, match="concrete compiled ProcedureContract"):
        ProcedureDefinition(
            "example.ExampleV1",
            "example.procedures:ExampleProcedure",
            "Example",
            None,
            ProcedureContract,
        )


@pytest.mark.parametrize(
    "target",
    [
        "example.procedures",
        ":ExampleProcedure",
        "example.procedures:",
        "example..procedures:ExampleProcedure",
        "example.procedures:.ExampleProcedure",
        "example.procedures:ExampleProcedure.",
        "example procedures:ExampleProcedure",
        "example.procedures:Example Procedure",
        "example.procedures:Example:Procedure",
    ],
)
def test_procedure_definition_requires_module_attribute_target(target: str) -> None:
    with pytest.raises(ValueError, match="module:attribute"):
        ProcedureDefinition(
            "example.ExampleV1",
            target,
            "Example",
            None,
            Contract,
        )


def test_procedure_definition_resolves_lazily_and_checks_resolution_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imports: list[str] = []

    def import_module(name: str) -> object:
        imports.append(name)
        return SimpleNamespace(nested=SimpleNamespace(Example=ExampleProcedure))

    monkeypatch.setattr("provium.procedure.definition.import_module", import_module)
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:nested.Example",
        "Example",
        None,
        Contract,
    )
    monkeypatch.setattr(
        ExampleProcedure,
        "definition",
        ProcedureDefinition(
            "example.ExampleV1",
            "example.procedures:nested.Example",
            "Different label",
            "Different description.",
            Contract,
        ),
    )

    assert imports == []
    assert definition.resolve() is ExampleProcedure
    assert definition.resolve() is ExampleProcedure
    assert imports == ["example.procedures"]


def test_procedure_definition_rejects_a_nonclass_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(value=object()),
    )
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )

    with pytest.raises(TypeError, match="must resolve to a Procedure class"):
        definition.resolve()


def test_procedure_definition_rejects_a_nonprocedure_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NotAProcedure:
        definition = EXAMPLE_DEFINITION

    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(value=NotAProcedure),
    )
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )

    with pytest.raises(TypeError, match="must resolve to a Procedure class"):
        definition.resolve()


def test_procedure_definition_requires_the_class_to_declare_its_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingDefinition(Procedure[Config, SetupInputs, Inputs, Outputs]):
        pass

    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(value=MissingDefinition),
    )
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )

    with pytest.raises(TypeError, match="must declare a ProcedureDefinition"):
        definition.resolve()


def test_procedure_definition_rejects_mismatched_generic_io_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OtherInputs(ProcedureInputs):
        pass

    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )

    class MismatchedProcedure(Procedure[Config, SetupInputs, OtherInputs, Outputs]):
        pass

    MismatchedProcedure.definition = definition

    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(value=MismatchedProcedure),
    )

    with pytest.raises(TypeError, match="generic specialization does not match"):
        definition.resolve()


def test_procedure_definition_rejects_an_unspecialized_procedure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )

    class UnspecializedProcedure(Procedure):
        pass

    UnspecializedProcedure.definition = definition
    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(value=UnspecializedProcedure),
    )

    with pytest.raises(TypeError, match="generic specialization does not match"):
        definition.resolve()


def test_procedure_definition_rejects_an_abstract_procedure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )

    class AbstractProcedure(Procedure[Config, SetupInputs, Inputs, Outputs]):
        pass

    AbstractProcedure.definition = definition
    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(value=AbstractProcedure),
    )

    with pytest.raises(TypeError, match="must resolve to a concrete Procedure class"):
        definition.resolve()


@pytest.mark.parametrize("hook", ["setup", "process"])
def test_procedure_definition_rejects_an_incompatible_lifecycle_hook(
    monkeypatch: pytest.MonkeyPatch,
    hook: str,
) -> None:
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )

    class InvalidSetup(Procedure[Config, SetupInputs, Inputs, Outputs]):
        def setup(self) -> None:
            pass

        def process(
            self,
            context: ProcedureProcessContext,
            configuration: Config,
            inputs: Inputs,
            outputs: Outputs,
        ) -> None:
            pass

    class InvalidProcess(Procedure[Config, SetupInputs, Inputs, Outputs]):
        def process(self) -> None:
            pass

    target = InvalidSetup if hook == "setup" else InvalidProcess
    target.definition = definition
    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(value=target),
    )

    with pytest.raises(TypeError, match=rf"procedure .* {hook} signature"):
        definition.resolve()


@pytest.mark.parametrize("descriptor", ["class", "static"])
def test_procedure_definition_requires_instance_lifecycle_methods(
    monkeypatch: pytest.MonkeyPatch,
    descriptor: str,
) -> None:
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )

    class ClassProcedure(Procedure[Config, SetupInputs, Inputs, Outputs]):
        @classmethod
        def process(
            cls,
            instance: object,
            context: ProcedureProcessContext,
            configuration: Config,
            inputs: Inputs,
            outputs: Outputs,
        ) -> None:
            pass

    class StaticProcedure(Procedure[Config, SetupInputs, Inputs, Outputs]):
        @staticmethod
        def process(
            instance: object,
            context: ProcedureProcessContext,
            configuration: Config,
            inputs: Inputs,
            outputs: Outputs,
        ) -> None:
            pass

    target = ClassProcedure if descriptor == "class" else StaticProcedure
    target.definition = definition
    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(value=target),
    )

    with pytest.raises(TypeError, match="process must be an instance method"):
        definition.resolve()


def test_procedure_definition_rejects_a_partial_generic_specialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )

    class PartialProcedure[InputsT: ProcedureInputs](
        Procedure[Config, SetupInputs, InputsT, Outputs]
    ):
        pass

    PartialProcedure.definition = definition
    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(value=PartialProcedure),
    )

    with pytest.raises(TypeError, match="generic specialization does not match"):
        definition.resolve()


def test_procedure_definition_detects_recursive_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )

    def import_module(name: str) -> object:
        definition.resolve()
        raise AssertionError("recursive resolution should fail first")

    monkeypatch.setattr("provium.procedure.definition.import_module", import_module)

    with pytest.raises(RuntimeError, match="recursive procedure resolution"):
        definition.resolve()


def test_procedure_definition_detects_recursive_contract_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        "example.contracts:Contract",
    )

    def import_module(name: str) -> object:
        definition.resolve_contract()
        raise AssertionError("recursive resolution should fail first")

    monkeypatch.setattr("provium.procedure.definition.import_module", import_module)

    with pytest.raises(RuntimeError, match="recursive contract resolution"):
        definition.resolve_contract()


def test_procedure_contract_resolution_is_thread_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        "example.contracts:Contract",
    )
    barrier = Barrier(4)
    imports: list[str] = []

    def import_module(name: str) -> object:
        imports.append(name)
        return SimpleNamespace(Contract=Contract)

    def resolve() -> type[ProcedureContract[Any]]:
        barrier.wait()
        return definition.resolve_contract()

    monkeypatch.setattr("provium.procedure.definition.import_module", import_module)

    with ThreadPoolExecutor(max_workers=4) as executor:
        resolved = list(executor.map(lambda _: resolve(), range(4)))

    assert resolved == [Contract] * 4
    assert imports == ["example.contracts"]


def test_contract_resolution_rechecks_cache_after_acquiring_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        "example.contracts:Contract",
    )

    class CacheOnEnter:
        def __enter__(self) -> None:
            object.__setattr__(definition, "_resolved_contract", Contract)

        def __exit__(self, *errors: object) -> None:
            pass

    monkeypatch.setattr(
        "provium.procedure.definition._PROCEDURE_RESOLUTION_LOCK",
        CacheOnEnter(),
    )

    assert definition.resolve_contract() is Contract


def test_equivalent_definitions_detect_recursive_target_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )
    second = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )
    imports = 0

    def import_module(name: str) -> object:
        nonlocal imports
        imports += 1
        if imports == 1:
            second.resolve()
        return SimpleNamespace(value=ExampleProcedure)

    monkeypatch.setattr("provium.procedure.definition.import_module", import_module)

    with pytest.raises(RuntimeError, match="recursive procedure resolution"):
        first.resolve()


def test_procedure_definition_resolution_is_thread_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )

    class Target(Procedure[Config, SetupInputs, Inputs, Outputs]):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: Config,
            inputs: Inputs,
            outputs: Outputs,
        ) -> None:
            pass

    Target.definition = definition
    barrier = Barrier(4)
    imports: list[str] = []

    def import_module(name: str) -> object:
        imports.append(name)
        return SimpleNamespace(value=Target)

    def resolve() -> type[Target]:
        barrier.wait()
        return definition.resolve()

    monkeypatch.setattr("provium.procedure.definition.import_module", import_module)

    with ThreadPoolExecutor(max_workers=4) as executor:
        resolved = list(executor.map(lambda _: resolve(), range(4)))

    assert resolved == [Target] * 4
    assert imports == ["example.procedures"]


def test_procedure_resolution_validates_each_unique_contract_artifact_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ArtifactContract(ProcedureContract[None]):
        configuration = None

        class SetupInputs(ProcedureInputs):
            setup = input(EXAMPLE_ARTIFACT)

        class Inputs(ProcedureInputs):
            first = input(EXAMPLE_ARTIFACT)
            second = input(EXAMPLE_ARTIFACT)

        class Outputs(ProcedureOutputs):
            result = output(EXAMPLE_ARTIFACT)

    definition = ProcedureDefinition(
        "example.ArtifactsV1",
        "example.procedures:value",
        "Artifacts",
        None,
        ArtifactContract,
    )

    class Target(
        Procedure[
            None,
            ArtifactContract.SetupInputs,
            ArtifactContract.Inputs,
            ArtifactContract.Outputs,
        ]
    ):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: None,
            inputs: ArtifactContract.Inputs,
            outputs: ArtifactContract.Outputs,
        ) -> None:
            pass

    Target.definition = definition
    resolutions: list[ArtifactDefinition[ExampleArtifact]] = []

    def resolve(
        artifact: ArtifactDefinition[ExampleArtifact],
    ) -> type[ExampleArtifact]:
        resolutions.append(artifact)
        return ExampleArtifact

    monkeypatch.setattr(ArtifactDefinition, "resolve", resolve)
    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(value=Target),
    )

    assert definition.resolve() is Target
    assert resolutions == [EXAMPLE_ARTIFACT]


def test_procedure_resolution_rejects_a_nonartifact_contract_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ArtifactContract(ProcedureContract[None]):
        configuration = None

        class Inputs(ProcedureInputs):
            value = input(EXAMPLE_ARTIFACT)

    definition = ProcedureDefinition(
        "example.ArtifactsV1",
        "example.procedures:value",
        "Artifacts",
        None,
        ArtifactContract,
    )

    class Target(
        Procedure[
            None,
            ArtifactContract.SetupInputs,
            ArtifactContract.Inputs,
            ArtifactContract.Outputs,
        ]
    ):
        pass

    Target.definition = definition
    monkeypatch.setattr(ArtifactDefinition, "resolve", lambda self: object())
    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(value=Target),
    )

    with pytest.raises(TypeError, match="example.ExampleArtifactV1.*Artifact class"):
        definition.resolve()


def test_procedure_resolution_rejects_mismatched_artifact_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ArtifactContract(ProcedureContract[None]):
        configuration = None

        class Inputs(ProcedureInputs):
            value = input(EXAMPLE_ARTIFACT)

    definition = ProcedureDefinition(
        "example.ArtifactsV1",
        "example.procedures:value",
        "Artifacts",
        None,
        ArtifactContract,
    )

    class Target(
        Procedure[
            None,
            ArtifactContract.SetupInputs,
            ArtifactContract.Inputs,
            ArtifactContract.Outputs,
        ]
    ):
        pass

    class OtherArtifact(Artifact[Reader, Writer]):
        definition = ArtifactDefinition(
            "example.OtherArtifactV1",
            "example.artifacts:OtherArtifact",
            "Another artifact.",
        )
        reader = Reader
        writer = Writer

    Target.definition = definition
    monkeypatch.setattr(
        ArtifactDefinition,
        "resolve",
        lambda self: OtherArtifact,
    )
    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(value=Target),
    )

    with pytest.raises(ValueError, match="mismatched definition metadata"):
        definition.resolve()


def test_procedure_resolution_validates_distinct_equivalent_definitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvalidDefinition(ArtifactDefinition[ExampleArtifact]):
        def resolve(self) -> type[ExampleArtifact]:
            return object()  # type: ignore[return-value]

    invalid_definition = InvalidDefinition(
        EXAMPLE_ARTIFACT.identifier,
        EXAMPLE_ARTIFACT.target,
        EXAMPLE_ARTIFACT.description,
    )

    class ArtifactContract(ProcedureContract[None]):
        configuration = None

        class Inputs(ProcedureInputs):
            valid = input(EXAMPLE_ARTIFACT)
            invalid = input(invalid_definition)

    definition = ProcedureDefinition(
        "example.ArtifactsV1",
        "example.procedures:value",
        "Artifacts",
        None,
        ArtifactContract,
    )

    class Target(
        Procedure[
            None,
            ArtifactContract.SetupInputs,
            ArtifactContract.Inputs,
            ArtifactContract.Outputs,
        ]
    ):
        pass

    Target.definition = definition
    monkeypatch.setattr(
        ArtifactDefinition,
        "resolve",
        lambda self: ExampleArtifact,
    )
    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(value=Target),
    )

    with pytest.raises(TypeError, match="must be an Artifact class"):
        definition.resolve()


def test_resolution_rechecks_the_cache_after_acquiring_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )

    class PopulateCacheOnEntry:
        def __enter__(self) -> None:
            object.__setattr__(definition, "_resolved_class", ExampleProcedure)

        def __exit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(
        "provium.procedure.definition._PROCEDURE_RESOLUTION_LOCK",
        PopulateCacheOnEntry(),
    )

    assert definition.resolve() is ExampleProcedure


def test_procedure_definition_rejects_mismatched_resolution_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:ExampleProcedure",
        "Example",
        None,
        Contract,
    )

    class Target(Procedure[Config, SetupInputs, Inputs, Outputs]):
        definition = ProcedureDefinition(
            "example.OtherV1",
            "example.procedures:value",
            "Example",
            None,
            Contract,
        )

    monkeypatch.setattr(
        "provium.procedure.definition.import_module",
        lambda name: SimpleNamespace(value=Target),
    )
    definition = ProcedureDefinition(
        "example.ExampleV1",
        "example.procedures:value",
        "Example",
        None,
        Contract,
    )

    with pytest.raises(ValueError, match="identifier, target"):
        definition.resolve()
