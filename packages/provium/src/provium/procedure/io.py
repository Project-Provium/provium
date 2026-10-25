"""Declarative procedure input and output fields."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import Any, ClassVar, Literal, Self, cast, overload

from provium.artifact import (
    Artifact,
    ArtifactDefinition,
    ArtifactReadBinding,
    ArtifactReader,
    ArtifactWriteBinding,
    ArtifactWriter,
)


class ProcedureIOField:
    """Shared immutable metadata for a declared procedure I/O field."""

    __slots__ = (
        "_artifact",
        "_description",
        "_direction",
        "_maximum",
        "_minimum",
        "_name",
        "_owner",
    )

    def __init__(
        self,
        artifact: ArtifactDefinition[Any],
        direction: Literal["input", "output"],
        description: str | None,
        *,
        minimum: int = 1,
        maximum: int | None = 1,
    ) -> None:
        if not isinstance(artifact, ArtifactDefinition):
            raise TypeError(
                "procedure I/O field artifact must be an ArtifactDefinition"
            )
        if direction not in ("input", "output"):
            raise ValueError("procedure I/O field direction must be input or output")
        if description is not None and not isinstance(description, str):
            raise TypeError("procedure I/O field description must be a string")
        if description is not None and not description.strip():
            raise ValueError("procedure I/O field description must be nonempty")
        if type(minimum) is not int:
            raise TypeError("procedure I/O field minimum must be an integer")
        if minimum < 0:
            raise ValueError("procedure I/O field minimum must be nonnegative")
        if maximum is not None and type(maximum) is not int:
            raise TypeError("procedure I/O field maximum must be an integer or None")
        if maximum == 0:
            raise ValueError(
                "procedure I/O field maximum must allow at least one artifact"
            )
        if maximum is not None and maximum < minimum:
            raise ValueError(
                "procedure I/O field maximum must be greater than or equal to minimum"
            )
        self._name: str | None = None
        self._owner: type[object] | None = None
        self._artifact: ArtifactDefinition[Any] = artifact
        self._direction: Literal["input", "output"] = direction
        self._description: str | None = description
        self._minimum = minimum
        self._maximum = maximum

    def __set_name__(self, owner: type[object], name: str) -> None:
        if self._owner is None:
            self._owner = owner
            self._name = name

    def is_declared_on(self, owner: type[object]) -> bool:
        """Return whether this descriptor was first assigned to ``owner``."""
        return self._owner is owner

    def _get_value(self, instance: object) -> object:
        try:
            values = cast(
                Mapping[str, object],
                object.__getattribute__(instance, "_values"),
            )
            return values[self.name]
        except AttributeError:
            raise AttributeError(
                f"procedure {self.direction} values are not constructed yet"
            ) from None

    @property
    def artifact(self) -> ArtifactDefinition[Any]:
        """Return the artifact definition required by this field."""
        return self._artifact

    @property
    def direction(self) -> Literal["input", "output"]:
        """Return whether this field reads an input or writes an output."""
        return self._direction

    @property
    def description(self) -> str | None:
        """Return the optional human-readable field description."""
        return self._description

    @property
    def name(self) -> str:
        """Return the attribute name assigned by the declaring class."""
        if self._name is None:
            raise AttributeError("procedure I/O field has not been assigned to a class")
        return self._name

    @property
    def minimum(self) -> int:
        """Return the required minimum number of bindings."""
        return self._minimum

    @property
    def maximum(self) -> int | None:
        """Return the permitted maximum number of bindings."""
        return self._maximum

    @property
    def required(self) -> bool:
        """Return whether the field requires a binding."""
        return self._minimum > 0

    @property
    def repeated(self) -> bool:
        """Return whether the field contains several ordered bindings."""
        return False


class ProcedureInputField[ReaderT: ArtifactReader](ProcedureIOField):
    """A required input field whose instance value will be a read binding."""

    __slots__ = ()

    def __init__[WriterT: ArtifactWriter](
        self,
        artifact: ArtifactDefinition[Artifact[ReaderT, WriterT]],
        *,
        description: str | None = None,
    ) -> None:
        super().__init__(artifact, "input", description)

    @overload
    def __get__(
        self, instance: None, owner: type[object]
    ) -> ProcedureInputField[ReaderT]: ...

    @overload
    def __get__(
        self, instance: object, owner: type[object] | None = None
    ) -> ArtifactReadBinding[ReaderT]: ...

    def __get__(
        self, instance: object | None, owner: type[object] | None = None
    ) -> Any:
        if instance is None:
            return self
        return self._get_value(instance)


class ProcedureOutputField[WriterT: ArtifactWriter](ProcedureIOField):
    """A required output field whose instance value will be a write binding."""

    __slots__ = ()

    def __init__[ReaderT: ArtifactReader](
        self,
        artifact: ArtifactDefinition[Artifact[ReaderT, WriterT]],
        *,
        description: str | None = None,
    ) -> None:
        super().__init__(artifact, "output", description)

    @overload
    def __get__(
        self, instance: None, owner: type[object]
    ) -> ProcedureOutputField[WriterT]: ...

    @overload
    def __get__(
        self, instance: object, owner: type[object] | None = None
    ) -> ArtifactWriteBinding[WriterT]: ...

    def __get__(
        self, instance: object | None, owner: type[object] | None = None
    ) -> Any:
        if instance is None:
            return self
        return self._get_value(instance)


class ProcedureOptionalInputField[ReaderT: ArtifactReader](ProcedureIOField):
    """An optional input field whose instance value may be absent."""

    __slots__ = ()

    def __init__[WriterT: ArtifactWriter](
        self,
        artifact: ArtifactDefinition[Artifact[ReaderT, WriterT]],
        *,
        description: str | None = None,
    ) -> None:
        super().__init__(artifact, "input", description, minimum=0)

    @overload
    def __get__(
        self, instance: None, owner: type[object]
    ) -> ProcedureOptionalInputField[ReaderT]: ...

    @overload
    def __get__(
        self, instance: object, owner: type[object] | None = None
    ) -> ArtifactReadBinding[ReaderT] | None: ...

    def __get__(
        self, instance: object | None, owner: type[object] | None = None
    ) -> Any:
        if instance is None:
            return self
        return self._get_value(instance)


class ProcedureOptionalOutputField[WriterT: ArtifactWriter](ProcedureIOField):
    """An optional output field whose instance value may be absent."""

    __slots__ = ()

    def __init__[ReaderT: ArtifactReader](
        self,
        artifact: ArtifactDefinition[Artifact[ReaderT, WriterT]],
        *,
        description: str | None = None,
    ) -> None:
        super().__init__(artifact, "output", description, minimum=0)

    @overload
    def __get__(
        self, instance: None, owner: type[object]
    ) -> ProcedureOptionalOutputField[WriterT]: ...

    @overload
    def __get__(
        self, instance: object, owner: type[object] | None = None
    ) -> ArtifactWriteBinding[WriterT] | None: ...

    def __get__(
        self, instance: object | None, owner: type[object] | None = None
    ) -> Any:
        if instance is None:
            return self
        return self._get_value(instance)


class ProcedureRepeatedInputField[ReaderT: ArtifactReader](ProcedureIOField):
    """A repeated input field whose instance value is an ordered tuple."""

    __slots__ = ()

    def __init__[WriterT: ArtifactWriter](
        self,
        artifact: ArtifactDefinition[Artifact[ReaderT, WriterT]],
        *,
        minimum: int = 0,
        maximum: int | None = None,
        description: str | None = None,
    ) -> None:
        super().__init__(
            artifact,
            "input",
            description,
            minimum=minimum,
            maximum=maximum,
        )

    @property
    def repeated(self) -> bool:
        """Return whether the field contains several ordered bindings."""
        return True

    @overload
    def __get__(
        self, instance: None, owner: type[object]
    ) -> ProcedureRepeatedInputField[ReaderT]: ...

    @overload
    def __get__(
        self, instance: object, owner: type[object] | None = None
    ) -> tuple[ArtifactReadBinding[ReaderT], ...]: ...

    def __get__(
        self, instance: object | None, owner: type[object] | None = None
    ) -> Any:
        if instance is None:
            return self
        return self._get_value(instance)


class _ProcedureIORecordMeta(type):
    """Keep every procedure I/O record subclass slot-based."""

    @staticmethod
    def _record_slots(namespace: Mapping[str, object]) -> tuple[str, ...]:
        declared = namespace.get("__slots__", ())
        slots = (
            (declared,)
            if isinstance(declared, str)
            else tuple(cast(Iterable[str], declared))
        )
        if "__dict__" in slots:
            raise TypeError(
                "procedure I/O record cannot declare an instance dictionary"
            )
        return slots

    def __new__(
        mcls,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> _ProcedureIORecordMeta:
        namespace["__slots__"] = mcls._record_slots(namespace)
        return super().__new__(mcls, name, bases, namespace, **kwargs)


class _ProcedureIORecord(metaclass=_ProcedureIORecordMeta):
    __slots__ = ("_values",)

    _direction: ClassVar[Literal["input", "output"]]
    fields: ClassVar[Mapping[str, ProcedureIOField]] = MappingProxyType({})
    _construction_methods: ClassVar[frozenset[str]] = frozenset(
        {
            "__init__",
            "_from_bindings",
            "_normalize_field_value",
            "_normalize_repeated_value",
            "_assert_binding_is_valid",
            "_assert_binding_artifact_is_valid",
            "_assert_binding_names_are_valid",
            "_assert_cardinality_is_valid",
        }
    )

    def __init__(self) -> None:
        raise TypeError("procedure I/O records must be constructed from bindings")

    @classmethod
    def _from_bindings(cls, bindings: Mapping[str, object]) -> Self:
        """Construct a record from executor-supplied bindings."""
        cls._assert_binding_names_are_valid(bindings)
        values = {
            name: cls._normalize_field_value(field, bindings.get(name))
            for name, field in cls.fields.items()
        }
        instance = object.__new__(cls)
        object.__setattr__(instance, "_values", MappingProxyType(values))
        return instance

    @classmethod
    def _assert_binding_names_are_valid(cls, bindings: Mapping[str, object]) -> None:
        if not isinstance(bindings, Mapping):
            raise TypeError("procedure I/O bindings must be a mapping")
        unknown = [name for name in bindings if name not in cls.fields]
        if unknown:
            raise TypeError(f"unknown field: {unknown[0]}")

    @classmethod
    def _normalize_field_value(
        cls,
        field: ProcedureIOField,
        supplied: object,
    ) -> object:
        if field.repeated:
            return cls._normalize_repeated_value(field, supplied)
        if supplied is None:
            if field.required:
                raise TypeError(f"missing required field: {field.name}")
            return None
        cls._assert_binding_is_valid(field, supplied)
        return supplied

    @classmethod
    def _normalize_repeated_value(
        cls,
        field: ProcedureIOField,
        supplied: object,
    ) -> tuple[object, ...]:
        if supplied is None:
            values: tuple[object, ...] = ()
        elif isinstance(supplied, Sequence) and not isinstance(
            supplied, (str, bytes, bytearray)
        ):
            values = tuple(cast(Sequence[object], supplied))
        else:
            raise TypeError(
                f"{field.name} must be a sequence of artifact read bindings"
            )
        cls._assert_cardinality_is_valid(field, len(values))
        for value in values:
            cls._assert_binding_is_valid(field, value)
        return values

    @staticmethod
    def _assert_cardinality_is_valid(field: ProcedureIOField, count: int) -> None:
        if count < field.minimum:
            raise ValueError(
                f"{field.name} requires at least {field.minimum} binding"
                f"{'s' if field.minimum != 1 else ''}"
            )
        if field.maximum is not None and count > field.maximum:
            raise ValueError(
                f"{field.name} permits at most {field.maximum} binding"
                f"{'s' if field.maximum != 1 else ''}"
            )

    @classmethod
    def _assert_binding_is_valid(cls, field: ProcedureIOField, binding: object) -> None:
        expected_type: type[object]
        if cls._direction == "input":
            expected_type = ArtifactReadBinding
            direction = "read"
        else:
            expected_type = ArtifactWriteBinding
            direction = "write"
        if not isinstance(binding, expected_type):
            raise TypeError(f"{field.name} must be an artifact {direction} binding")
        cls._assert_binding_artifact_is_valid(
            field,
            cast(ArtifactReadBinding[Any] | ArtifactWriteBinding[Any], binding),
        )

    @staticmethod
    def _assert_binding_artifact_is_valid(
        field: ProcedureIOField,
        binding: ArtifactReadBinding[Any] | ArtifactWriteBinding[Any],
    ) -> None:
        expected = (field.artifact.identifier, field.artifact.target)
        actual_definition = binding.artifact.definition
        actual = (actual_definition.identifier, actual_definition.target)
        if actual != expected:
            raise TypeError(
                f"{field.name} must bind artifact {field.artifact.identifier}"
            )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("procedure I/O records are immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("procedure I/O records are immutable")

    def __repr__(self) -> str:
        values = ", ".join(f"{name}={self._values[name]!r}" for name in self.fields)
        return f"{type(self).__name__}({values})"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._assert_construction_declaration_is_valid()
        cls._assert_immutability_declaration_is_valid()
        cls._assert_direction_declaration_is_valid()
        fields, inherited_candidates, conflicts = cls._collect_inherited_fields()
        cls._merge_declared_fields(fields, inherited_candidates, conflicts)
        cls._assert_undeclared_annotations_are_valid()
        cls._assert_collected_fields_are_valid(fields, conflicts)
        cls.fields = MappingProxyType(fields)

    @classmethod
    def _assert_construction_declaration_is_valid(cls) -> None:
        if _ProcedureIORecord._construction_methods & cls.__dict__.keys():
            raise TypeError("procedure I/O record cannot override binding construction")

    @classmethod
    def _assert_immutability_declaration_is_valid(cls) -> None:
        if "__setattr__" in cls.__dict__ or "__delattr__" in cls.__dict__:
            raise TypeError("procedure I/O record cannot override record immutability")

    @classmethod
    def _assert_direction_declaration_is_valid(cls) -> None:
        if "_direction" in cls.__dict__ and _ProcedureIORecord not in cls.__bases__:
            raise TypeError("procedure I/O record direction cannot be overridden")
        inherited_directions = {
            direction
            for base in cls.__bases__
            if issubclass(base, _ProcedureIORecord)
            and (direction := getattr(base, "_direction", None)) is not None
        }
        if len(inherited_directions) > 1:
            raise TypeError("procedure I/O record has conflicting record directions")

    @classmethod
    def _collect_inherited_fields(
        cls,
    ) -> tuple[
        dict[str, ProcedureIOField],
        dict[str, list[ProcedureIOField]],
        set[str],
    ]:
        fields: dict[str, ProcedureIOField] = {}
        inherited_candidates: dict[str, list[ProcedureIOField]] = {}
        for base in cls.__bases__:
            if not issubclass(base, _ProcedureIORecord):
                continue
            for name, field in getattr(base, "fields", {}).items():
                fields.setdefault(name, field)
                inherited_candidates.setdefault(name, []).append(field)
        conflicts = {
            name
            for name, candidates in inherited_candidates.items()
            if len({id(candidate) for candidate in candidates}) > 1
        }
        return fields, inherited_candidates, conflicts

    @classmethod
    def _merge_declared_fields(
        cls,
        fields: dict[str, ProcedureIOField],
        inherited_candidates: Mapping[str, list[ProcedureIOField]],
        conflicts: set[str],
    ) -> None:
        local_field_ids: set[int] = set()
        for name, value in cls.__dict__.items():
            if name.startswith("_"):
                continue
            value = cls._assert_declared_field_is_valid(name, value, local_field_ids)
            cls._assert_field_override_is_valid(
                name,
                value,
                inherited_candidates.get(name, ()),
            )
            fields[name] = value
            conflicts.discard(name)

    @classmethod
    def _assert_declared_field_is_valid(
        cls,
        name: str,
        value: object,
        local_field_ids: set[int],
    ) -> ProcedureIOField:
        if not isinstance(value, ProcedureIOField):
            raise TypeError(f"{cls.__name__}.{name} must be a procedure I/O field")
        if not callable(getattr(type(value), "__get__", None)):
            raise TypeError(
                f"{cls.__name__}.{name} must use a concrete procedure I/O field"
            )
        if not value.is_declared_on(cls) or id(value) in local_field_ids:
            raise TypeError("procedure I/O field descriptor cannot be reused")
        local_field_ids.add(id(value))
        return value

    @classmethod
    def _assert_field_override_is_valid(
        cls,
        name: str,
        value: ProcedureIOField,
        inherited: Sequence[ProcedureIOField],
    ) -> None:
        inherited_artifacts = {
            (candidate.artifact.identifier, candidate.artifact.target)
            for candidate in inherited
        }
        if (
            inherited_artifacts
            and (
                value.artifact.identifier,
                value.artifact.target,
            )
            not in inherited_artifacts
        ):
            raise TypeError(f"{cls.__name__}.{name} cannot change artifact")

        inherited_cardinalities = {
            (candidate.minimum, candidate.maximum) for candidate in inherited
        }
        if (
            inherited_cardinalities
            and (
                value.minimum,
                value.maximum,
            )
            not in inherited_cardinalities
        ):
            raise TypeError(f"{cls.__name__}.{name} cannot change cardinality")

        inherited_shapes = {candidate.repeated for candidate in inherited}
        if inherited_shapes and value.repeated not in inherited_shapes:
            raise TypeError(f"{cls.__name__}.{name} cannot change binding shape")

    @classmethod
    def _assert_undeclared_annotations_are_valid(cls) -> None:
        annotations = cls.__dict__.get("__annotations__", {})
        for name in annotations:
            if not name.startswith("_") and name not in cls.__dict__:
                raise TypeError(f"{cls.__name__}.{name} must be a procedure I/O field")

    @classmethod
    def _assert_collected_fields_are_valid(
        cls,
        fields: Mapping[str, ProcedureIOField],
        conflicts: set[str],
    ) -> None:
        for name, field in fields.items():
            if name in conflicts:
                raise TypeError(
                    f"{cls.__name__}.{name} has conflicting inherited fields"
                )
            if field.direction != cls._direction:
                raise TypeError(
                    f"{cls.__name__}.{name} must be an {cls._direction} field"
                )


class ProcedureInputs(_ProcedureIORecord):
    """Base type for a procedure's declared input record."""

    _direction = "input"


class ProcedureOutputs(_ProcedureIORecord):
    """Base type for a procedure's declared output record."""

    _direction = "output"


def build_procedure_inputs[InputsT: ProcedureInputs](
    record_type: type[InputsT],
    bindings: Mapping[str, object],
) -> InputsT:
    """Construct a typed input record for the procedure executor."""
    return record_type._from_bindings(bindings)  # pyright: ignore[reportPrivateUsage]


def build_procedure_outputs[OutputsT: ProcedureOutputs](
    record_type: type[OutputsT],
    bindings: Mapping[str, object],
) -> OutputsT:
    """Construct a typed output record for the procedure executor."""
    return record_type._from_bindings(bindings)  # pyright: ignore[reportPrivateUsage]


def input[ReaderT: ArtifactReader, WriterT: ArtifactWriter](
    artifact: ArtifactDefinition[Artifact[ReaderT, WriterT]],
    *,
    description: str | None = None,
) -> ProcedureInputField[ReaderT]:
    """Declare one required procedure input."""
    return ProcedureInputField(artifact, description=description)


def output[ReaderT: ArtifactReader, WriterT: ArtifactWriter](
    artifact: ArtifactDefinition[Artifact[ReaderT, WriterT]],
    *,
    description: str | None = None,
) -> ProcedureOutputField[WriterT]:
    """Declare one required procedure output."""
    return ProcedureOutputField(artifact, description=description)


def optional_input[ReaderT: ArtifactReader, WriterT: ArtifactWriter](
    artifact: ArtifactDefinition[Artifact[ReaderT, WriterT]],
    *,
    description: str | None = None,
) -> ProcedureOptionalInputField[ReaderT]:
    """Declare one optional procedure input."""
    return ProcedureOptionalInputField(artifact, description=description)


def optional_output[ReaderT: ArtifactReader, WriterT: ArtifactWriter](
    artifact: ArtifactDefinition[Artifact[ReaderT, WriterT]],
    *,
    description: str | None = None,
) -> ProcedureOptionalOutputField[WriterT]:
    """Declare one optional procedure output."""
    return ProcedureOptionalOutputField(artifact, description=description)


def repeated_input[ReaderT: ArtifactReader, WriterT: ArtifactWriter](
    artifact: ArtifactDefinition[Artifact[ReaderT, WriterT]],
    *,
    minimum: int = 0,
    maximum: int | None = None,
    description: str | None = None,
) -> ProcedureRepeatedInputField[ReaderT]:
    """Declare an ordered collection of procedure inputs."""
    return ProcedureRepeatedInputField(
        artifact,
        minimum=minimum,
        maximum=maximum,
        description=description,
    )


__all__ = [
    "ProcedureIOField",
    "ProcedureInputField",
    "ProcedureInputs",
    "ProcedureOptionalInputField",
    "ProcedureOptionalOutputField",
    "ProcedureOutputField",
    "ProcedureOutputs",
    "ProcedureRepeatedInputField",
    "input",
    "optional_input",
    "optional_output",
    "output",
    "repeated_input",
]
