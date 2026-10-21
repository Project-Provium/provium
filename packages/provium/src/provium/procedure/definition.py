"""Lightweight procedure contracts and definitions."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from importlib import import_module
from inspect import getattr_static, isabstract, isfunction, signature
from shlex import quote
from threading import RLock, local
from typing import Any, ClassVar, Literal, cast, get_args, get_origin

from provium.artifact import ArtifactDefinition
from provium.artifact.binding import validate_artifact_class

from .config import ProcedureConfig
from .context import ProcedureProcessContext, ProcedureSetupContext
from .io import ProcedureInputs, ProcedureIOField, ProcedureOutputs


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be nonempty")


def _is_dotted_identifier(value: str) -> bool:
    return all(component.isidentifier() for component in value.split("."))


_CONTRACT_COMPILATION_HOOKS = frozenset(
    {
        "__init_subclass__",
        "_configuration_specialization",
        "_validate_configuration_specialization",
        "_validate_io_declarations",
        "_validated_configuration",
    }
)


class _ResolutionState(local):
    targets: set[str]
    contracts: set[str]

    def __init__(self) -> None:
        self.targets = set()
        self.contracts = set()


_PROCEDURE_RESOLUTION_LOCK = RLock()
_PROCEDURE_RESOLUTION_STATE = _ResolutionState()


@dataclass(frozen=True, slots=True)
class ProcedureIOFieldMetadata:
    """Immutable inspection metadata for one procedure I/O field."""

    name: str
    artifact_identifier: str
    artifact_description: str
    direction: Literal["input", "output"]
    minimum: int
    maximum: int | None
    repeated: bool
    description: str | None

    @property
    def required(self) -> bool:
        """Return whether the field requires at least one binding."""
        return self.minimum > 0


@dataclass(frozen=True, slots=True)
class ProcedureContractMetadata:
    """Immutable, implementation-free metadata compiled from a contract."""

    configuration_target: str | None
    _configuration_schema_json: str | None
    configuration_schema_digest: str | None
    setup_inputs: tuple[ProcedureIOFieldMetadata, ...]
    inputs: tuple[ProcedureIOFieldMetadata, ...]
    outputs: tuple[ProcedureIOFieldMetadata, ...]
    digest: str

    @property
    def configuration_schema(self) -> dict[str, object] | None:
        """Return an isolated, JSON-compatible configuration schema."""
        if self._configuration_schema_json is None:
            return None
        return cast(dict[str, object], json.loads(self._configuration_schema_json))


def _canonical_json(value: object) -> tuple[object, str]:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return json.loads(encoded), sha256(encoded).hexdigest()


def _compile_field_metadata(
    fields: Mapping[str, ProcedureIOField],
) -> tuple[ProcedureIOFieldMetadata, ...]:
    return tuple(
        ProcedureIOFieldMetadata(
            name=name,
            artifact_identifier=field.artifact.identifier,
            artifact_description=field.artifact.description,
            direction=field.direction,
            minimum=field.minimum,
            maximum=field.maximum,
            repeated=field.repeated,
            description=field.description,
        )
        for name, field in fields.items()
    )


def _field_metadata_payload(
    fields: tuple[ProcedureIOFieldMetadata, ...],
) -> list[dict[str, object]]:
    return [
        {
            "name": field.name,
            "artifact_identifier": field.artifact_identifier,
            "artifact_description": field.artifact_description,
            "direction": field.direction,
            "minimum": field.minimum,
            "maximum": field.maximum,
            "repeated": field.repeated,
            "description": field.description,
        }
        for field in fields
    ]


def _compile_contract_metadata(
    configuration: type[ProcedureConfig] | None,
    setup_inputs: type[ProcedureInputs] | None,
    inputs: type[ProcedureInputs] | None,
    outputs: type[ProcedureOutputs] | None,
) -> ProcedureContractMetadata:
    configuration_target: str | None = None
    configuration_schema_json: str | None = None
    configuration_schema_digest: str | None = None
    normalized_schema: object = None
    if configuration is not None:
        configuration_target = (
            f"{configuration.__module__}:{configuration.__qualname__}"
        )
        normalized_schema, configuration_schema_digest = _canonical_json(
            configuration.model_json_schema()
        )
        configuration_schema_json = json.dumps(
            normalized_schema,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    setup_metadata = (
        _compile_field_metadata(setup_inputs.fields) if setup_inputs else ()
    )
    input_metadata = _compile_field_metadata(inputs.fields) if inputs else ()
    output_metadata = _compile_field_metadata(outputs.fields) if outputs else ()
    payload = {
        "configuration_target": configuration_target,
        "configuration_schema": normalized_schema if configuration else None,
        "configuration_schema_digest": configuration_schema_digest,
        "setup_inputs": _field_metadata_payload(setup_metadata),
        "inputs": _field_metadata_payload(input_metadata),
        "outputs": _field_metadata_payload(output_metadata),
    }
    _, digest = _canonical_json(payload)
    return ProcedureContractMetadata(
        configuration_target=configuration_target,
        _configuration_schema_json=configuration_schema_json,
        configuration_schema_digest=configuration_schema_digest,
        setup_inputs=setup_metadata,
        inputs=input_metadata,
        outputs=output_metadata,
        digest=digest,
    )


def _format_binding_argument(
    flag: str,
    field: ProcedureIOFieldMetadata,
) -> str:
    argument = f"{flag} {field.name}=PATH"
    if field.repeated:
        maximum = "unbounded" if field.maximum is None else str(field.maximum)
        ellipsis = " ..." if field.maximum is None or field.maximum > 1 else ""
        repeated_argument = f"{argument}{ellipsis}"
        if not field.required:
            repeated_argument = f"[{repeated_argument}]"
        return f"{repeated_argument} ({field.minimum}..{maximum} bindings)"
    return argument if field.required else f"[{argument}]"


def _invocation_arguments(
    metadata: ProcedureContractMetadata,
) -> list[str]:
    arguments = [
        _format_binding_argument("--setup-input", field)
        for field in metadata.setup_inputs
    ]
    arguments.extend(
        _format_binding_argument("--input", field) for field in metadata.inputs
    )
    arguments.extend(
        _format_binding_argument("--output", field) for field in metadata.outputs
    )
    if metadata.configuration_target is not None:
        arguments.append("[--procedure-config FILE ...]")
    return arguments


class ProcedureContract[ConfigT: ProcedureConfig | None]:
    """Lightweight configuration and I/O contract for a procedure."""

    configuration: type[ConfigT] | None = None
    SetupInputs: ClassVar[type[ProcedureInputs] | None] = ProcedureInputs
    Inputs: ClassVar[type[ProcedureInputs] | None] = ProcedureInputs
    Outputs: ClassVar[type[ProcedureOutputs] | None] = ProcedureOutputs
    metadata: ClassVar[ProcedureContractMetadata]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # Validate the declaration here so a malformed plugin fails
        # while it is imported, rather than later during procedure execution.
        super().__init_subclass__(**kwargs)
        if _CONTRACT_COMPILATION_HOOKS & cls.__dict__.keys():
            raise TypeError("procedure contract cannot override contract compilation")
        configuration = cls._validated_configuration()
        cls._validate_io_declarations()
        cls._validate_configuration_specialization(
            configuration,
            cls._configuration_specialization(),
        )
        cls.metadata = _compile_contract_metadata(
            configuration,
            cls.SetupInputs,
            cls.Inputs,
            cls.Outputs,
        )

    @classmethod
    def _validated_configuration(cls) -> type[ProcedureConfig] | None:
        configuration = cls.configuration
        if configuration is not None and (
            not isinstance(configuration, type)
            or not issubclass(configuration, ProcedureConfig)
        ):
            raise TypeError(
                "procedure contract configuration must be a ProcedureConfig "
                "class or None"
            )
        return cast(type[ProcedureConfig] | None, configuration)

    @classmethod
    def _validate_io_declarations(cls) -> None:
        for name in ("SetupInputs", "Inputs"):
            value = getattr(cls, name)
            if value is not None and (
                not isinstance(value, type) or not issubclass(value, ProcedureInputs)
            ):
                raise TypeError(
                    f"procedure contract {name} must be a ProcedureInputs class or None"
                )
        outputs = cls.Outputs
        if outputs is not None and (
            not isinstance(outputs, type) or not issubclass(outputs, ProcedureOutputs)
        ):
            raise TypeError(
                "procedure contract Outputs must be a ProcedureOutputs class or None"
            )
        if cls.SetupInputs is None and cls.Inputs is None and cls.Outputs is None:
            raise TypeError(
                "procedure contract must have at least one setup input, "
                "processing input, or output declaration"
            )

    @classmethod
    def _configuration_specialization(cls) -> type[object]:
        # Find the concrete ConfigT supplied by ProcedureContract[ConfigT],
        # including specializations inherited through helper contract classes.
        specializations: set[type[object]] = set()
        for base in cls.__mro__:
            for generic_base in getattr(base, "__orig_bases__", ()):
                if get_origin(generic_base) is ProcedureContract:
                    arguments = get_args(generic_base)
                    # A remaining TypeVar means this is still a generic helper
                    # class, not a concrete contract usable by the executor.
                    if len(arguments) == 1 and isinstance(arguments[0], type):
                        specializations.add(arguments[0])
        if len(specializations) != 1:
            raise TypeError(
                "procedure contract must have exactly one generic specialization"
            )
        return next(iter(specializations))

    @staticmethod
    def _validate_configuration_specialization(
        configuration: type[ProcedureConfig] | None,
        expected_configuration: type[object],
    ) -> None:
        # ProcedureContract[None] is represented at runtime by NoneType, while
        # its declaration stores the value None. Configured contracts instead
        # store the exact model class named by their generic specialization.
        if (
            configuration is None
            and expected_configuration is not type(None)
            or configuration is not None
            and configuration is not expected_configuration
        ):
            raise TypeError(
                "procedure contract configuration does not match its generic "
                "specialization"
            )


class Procedure[
    ConfigT: ProcedureConfig | None,
    SetupInputsT: ProcedureInputs | None,
    InputsT: ProcedureInputs | None,
    OutputsT: ProcedureOutputs | None,
](ABC):
    """Base type for a concrete procedure implementation."""

    definition: ClassVar[ProcedureDefinition[Any]]

    def setup(
        self,
        context: ProcedureSetupContext,
        configuration: ConfigT,
        inputs: SetupInputsT,
    ) -> None:
        """Prepare reusable state before processing begins."""

    @abstractmethod
    def process(
        self,
        context: ProcedureProcessContext,
        configuration: ConfigT,
        inputs: InputsT,
        outputs: OutputsT,
    ) -> None:
        """Process one invocation."""

    def close(self) -> None:
        """Release state prepared by :meth:`setup`."""


@dataclass(frozen=True, slots=True)
class ProcedureDefinition[ProcedureT: Procedure[Any, Any, Any, Any]]:
    """Describe a procedure implementation without importing it eagerly."""

    identifier: str
    target: str
    label: str
    description: str | None
    contract: str | type[ProcedureContract[Any]]
    _resolved_class: type[Procedure[Any, Any, Any, Any]] | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _resolved_contract: type[ProcedureContract[Any]] | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _require_text(self.identifier, "procedure definition identifier")
        _require_text(self.target, "procedure definition target")
        _require_text(self.label, "procedure definition label")
        if self.description is not None:
            _require_text(self.description, "procedure definition description")
        if isinstance(self.contract, str):
            self._validate_reference(self.contract, "contract")
        else:
            self._validate_contract_class(self.contract)

        self._validate_reference(self.target, "target")

    @staticmethod
    def _validate_reference(reference: str, field_name: str) -> None:
        module_name, separator, attribute_path = reference.partition(":")
        if (
            not separator
            or not _is_dotted_identifier(module_name)
            or not _is_dotted_identifier(attribute_path)
            or ":" in attribute_path
        ):
            raise ValueError(
                f"procedure definition {field_name} must use 'module:attribute' syntax"
            )

    @staticmethod
    def _validate_contract_class(contract: object) -> None:
        if (
            not isinstance(contract, type)
            or not issubclass(contract, ProcedureContract)
            or not isinstance(
                getattr(cast(type[Any], contract), "metadata", None),
                ProcedureContractMetadata,
            )
        ):
            raise TypeError(
                "procedure definition contract must be a concrete compiled "
                "ProcedureContract class"
            )

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        """Serialize public metadata while rebuilding local resolution state."""
        return (
            type(self),
            (
                self.identifier,
                self.target,
                self.label,
                self.description,
                self.contract,
            ),
        )

    @property
    def invocation_synopsis(self) -> str:
        """Return a direct-execution synopsis without resolving implementations."""
        lines = [f"provium execute {quote(self.identifier)}"]
        lines.extend(_invocation_arguments(self.resolve_contract().metadata))
        return " \\\n  ".join(lines)

    def resolve_contract(self) -> type[ProcedureContract[Any]]:
        """Import, validate, and cache the procedure contract when needed."""
        if not isinstance(self.contract, str):
            return self.contract
        if self._resolved_contract is not None:
            return self._resolved_contract
        with _PROCEDURE_RESOLUTION_LOCK:
            if self._resolved_contract is not None:
                return self._resolved_contract
            if self.contract in _PROCEDURE_RESOLUTION_STATE.contracts:
                raise RuntimeError(
                    f"recursive contract resolution for {self.identifier}"
                )
            _PROCEDURE_RESOLUTION_STATE.contracts.add(self.contract)
            try:
                resolved = self._import_reference(self.contract)
                self._validate_contract_class(resolved)
                resolved_contract = cast(type[ProcedureContract[Any]], resolved)
                object.__setattr__(self, "_resolved_contract", resolved_contract)
                return resolved_contract
            finally:
                _PROCEDURE_RESOLUTION_STATE.contracts.remove(self.contract)

    def resolve(self) -> type[ProcedureT]:
        """Import and return the procedure class described by this definition."""
        if self._resolved_class is not None:
            return cast(type[ProcedureT], self._resolved_class)
        with _PROCEDURE_RESOLUTION_LOCK:
            if self._resolved_class is not None:
                return cast(type[ProcedureT], self._resolved_class)
            if self.target in _PROCEDURE_RESOLUTION_STATE.targets:
                raise RuntimeError(
                    f"recursive procedure resolution for {self.identifier}"
                )
            _PROCEDURE_RESOLUTION_STATE.targets.add(self.target)
            try:
                resolved = self._import_target()
                self._validate_resolved_class(resolved)
                resolved_class = cast(type[Procedure[Any, Any, Any, Any]], resolved)
                self._validate_resolved_definition(resolved_class)
                self._validate_resolved_specialization(resolved_class)
                self._validate_contract_artifacts()
                self._validate_resolved_concrete(resolved_class)
                self._validate_lifecycle_signatures(resolved_class)
                object.__setattr__(self, "_resolved_class", resolved_class)
                return cast(type[ProcedureT], resolved_class)
            finally:
                _PROCEDURE_RESOLUTION_STATE.targets.remove(self.target)

    def _import_target(self) -> object:
        return self._import_reference(self.target)

    @staticmethod
    def _import_reference(reference: str) -> object:
        module_name, _, attribute_path = reference.partition(":")
        resolved: object = import_module(module_name)
        for component in attribute_path.split("."):
            resolved = getattr(resolved, component)
        return resolved

    def _validate_resolved_class(self, resolved: object) -> None:
        if not isinstance(resolved, type) or not issubclass(resolved, Procedure):
            raise TypeError(
                f"procedure definition {self.identifier} must resolve to a "
                "Procedure class"
            )

    def _validate_resolved_definition(
        self,
        resolved: type[Procedure[Any, Any, Any, Any]],
    ) -> None:
        resolved_definition = getattr(resolved, "definition", None)
        if not isinstance(resolved_definition, ProcedureDefinition):
            raise TypeError(
                f"procedure {self.identifier} must declare a ProcedureDefinition"
            )
        definition = cast(ProcedureDefinition[Any], resolved_definition)
        contract = self.resolve_contract()
        resolved_contract = definition.resolve_contract()
        if (
            resolved_contract.metadata.digest != contract.metadata.digest
            or definition.identifier != self.identifier
            or definition.target != self.target
        ):
            raise ValueError(
                "resolved procedure definition identifier, target, and contract "
                "must match"
            )

    def _validate_resolved_specialization(
        self,
        resolved: type[Procedure[Any, Any, Any, Any]],
    ) -> None:
        specializations: set[tuple[type[object], ...]] = set()
        for base in resolved.__mro__:
            for generic_base in getattr(base, "__orig_bases__", ()):
                if get_origin(generic_base) is Procedure:
                    arguments = get_args(generic_base)
                    if len(arguments) == 4 and all(
                        isinstance(argument, type) for argument in arguments
                    ):
                        specializations.add(cast(tuple[type[object], ...], arguments))
        contract = self.resolve_contract()
        configuration = cast(
            type[ProcedureConfig] | None,
            getattr(contract, "configuration"),
        )
        expected = (
            type(None) if configuration is None else configuration,
            type(None) if contract.SetupInputs is None else contract.SetupInputs,
            type(None) if contract.Inputs is None else contract.Inputs,
            type(None) if contract.Outputs is None else contract.Outputs,
        )
        if specializations != {expected}:
            raise TypeError(
                f"procedure {self.identifier} generic specialization does not match "
                "its contract"
            )

    def _validate_resolved_concrete(
        self,
        resolved: type[Procedure[Any, Any, Any, Any]],
    ) -> None:
        if isabstract(resolved):
            raise TypeError(
                f"procedure definition {self.identifier} must resolve to a "
                "concrete Procedure class"
            )

    def _validate_lifecycle_signatures(
        self,
        resolved: type[Procedure[Any, Any, Any, Any]],
    ) -> None:
        self._validate_hook_call_shape(resolved, "setup", 4)
        self._validate_hook_call_shape(resolved, "process", 5)

    def _validate_hook_call_shape(
        self,
        resolved: type[Procedure[Any, Any, Any, Any]],
        hook: str,
        positional_count: int,
    ) -> None:
        if not isfunction(getattr_static(resolved, hook)):
            raise TypeError(
                f"procedure {self.identifier} {hook} must be an instance method"
            )
        arguments = [object()] * positional_count
        try:
            signature(getattr(resolved, hook)).bind(*arguments)
        except TypeError as error:
            raise TypeError(
                f"procedure {self.identifier} {hook} signature is incompatible "
                "with its lifecycle hook"
            ) from error

    def _validate_contract_artifacts(self) -> None:
        definitions = self._contract_artifact_definitions()
        for definition in definitions.values():
            resolved = definition.resolve()
            try:
                artifact_class = validate_artifact_class(resolved)
            except TypeError as error:
                raise TypeError(
                    f"artifact {definition.identifier} resolved for procedure "
                    f"{self.identifier} is invalid: {error}"
                ) from error
            resolved_definition = artifact_class.definition
            if (
                resolved_definition.identifier != definition.identifier
                or resolved_definition.target != definition.target
            ):
                raise ValueError(
                    f"artifact {definition.identifier} resolved for procedure "
                    f"{self.identifier} with mismatched definition metadata"
                )

    def _contract_artifact_definitions(
        self,
    ) -> dict[int, ArtifactDefinition[Any]]:
        definitions: dict[int, ArtifactDefinition[Any]] = {}
        contract = self.resolve_contract()
        records = (contract.SetupInputs, contract.Inputs, contract.Outputs)
        for record in records:
            if record is None:
                continue
            for io_field in record.fields.values():
                definition = io_field.artifact
                definitions.setdefault(id(definition), definition)
        return definitions


__all__ = [
    "Procedure",
    "ProcedureContract",
    "ProcedureContractMetadata",
    "ProcedureDefinition",
    "ProcedureIOFieldMetadata",
    "ProcedureInputs",
    "ProcedureOutputs",
]
