"""Engine selection: deciding whether a transform runs on the CPU or the GPU.

*Mode* is the output geometry. *Engine* is where it computes. The two are
independent kwargs, and only ``traditional`` has a GPU path at all (ADR-0002,
decision 2).

Lives in its own module so that :mod:`zarrmony_snouty.adapter` and
:mod:`zarrmony_snouty.session` share one resolver without either importing
the other.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from . import _deshear_gpu
from ._errors import SnoutyEngineError
from ._metadata import SnoutyMetadata

#: What a caller may ask for. ``auto`` is not a third engine — it resolves to
#: ``cpu`` or ``gpu`` before any pixel work starts.
Engine = Literal["auto", "cpu", "gpu"]
ENGINES: tuple[Engine, ...] = ("auto", "cpu", "gpu")

#: What a resolver returns. No ``auto`` survives resolution.
ResolvedEngine = Literal["cpu", "gpu"]


def validate(engine: str) -> Engine:
    """Return ``engine`` unchanged, or raise on a value this package does not
    know.

    Raises:
        SnoutyEngineError: ``engine`` is not one of :data:`ENGINES`.
    """
    if engine not in ENGINES:
        raise SnoutyEngineError(
            f"unknown SnoutyReader engine {engine!r}; expected one of {list(ENGINES)}"
        )
    return engine  # type: ignore[return-value]


#: The one mode with a GPU path (ADR-0002, decision 2). A GPU deshear would
#: cost more in PCIe upload than the 0.15 s per timepoint the CPU deshear takes.
GPU_MODE = "traditional"


def required_device_bytes(meta: SnoutyMetadata, *, itemsize: int) -> int:
    """Device bytes one scene's ``traditional`` transform needs.

    Per channel, not per volume. The GPU wrapper transforms one channel at a
    time and the device lock serializes those calls, so peak device demand is
    one channel's worth however many channels the acquisition has.
    """
    return _deshear_gpu.required_device_bytes(
        size_z=meta.size_z,
        size_y=meta.size_y,
        size_x=meta.size_x,
        scan_step_size_px=meta.scan_step_size_px,
        voxel_aspect_ratio=meta.voxel_aspect_ratio,
        itemsize=itemsize,
    )


#: Key the engine record sits under inside ``reader.acquisition_audit``.
#: Namespaced, because zarrmony composes that dict from several readers with
#: ``setdefault`` and validates nothing, so a flat key could collide.
AUDIT_KEY = "zarrmony_snouty"


def audit_payload(
    engine_used: ResolvedEngine, fallback_reason: str | None
) -> dict[str, str | None]:
    """The provenance record for one scene's engine decision.

    Four fields: what ran, why it was not the GPU, and the two library
    versions. The versions are recorded whichever engine ran, because they
    describe the environment that made the decision. A convert that fell back
    for a missing cupy and one that fell back for a small card are different
    events, and only the record tells them apart afterwards.
    """
    return {
        "engine_used": engine_used,
        "engine_fallback_reason": fallback_reason,
        "cupy_version": _deshear_gpu.cupy_version(),
        "cuda_runtime_version": _deshear_gpu.cuda_runtime_version(),
    }


def _format_bytes(count: int) -> str:
    """Render a byte count in the largest unit that keeps it above 1.

    Device memory is a GB-scale figure in production and a kB-scale figure on
    a synthetic fixture. One adaptive formatter keeps both readable.
    """
    for unit, scale in (("GB", 1000**3), ("MB", 1000**2), ("kB", 1000)):
        if count >= scale:
            return f"{count / scale:.2f} {unit}"
    return f"{count} bytes"


def resolve(
    engine: Engine, mode: str, required_bytes: Callable[[], int]
) -> tuple[ResolvedEngine, str | None]:
    """Decide the engine up front, and say why when the answer is not the GPU.

    Returns ``(resolved_engine, fallback_reason)``. The reason is ``None``
    when nothing was given up.

    The mode check comes first, so a mode with no GPU path answers the same
    way on every host. The two environment checks come next, and they are the
    only two conditions that make an explicit ``engine="gpu"`` raise.

    ``required_bytes`` is a callable, not an int, because only the last branch
    needs it. Reading a scene's geometry costs a sidecar parse per scene, and
    a session reader must not pay that for a convert whose engine was already
    decided by its mode or by a missing cupy.
    """
    if engine == "cpu":
        return "cpu", None
    if mode != GPU_MODE:
        return "cpu", f"mode {mode!r} has no GPU path"
    if not _deshear_gpu.cupy_available:
        if engine == "gpu":
            raise SnoutyEngineError(
                "engine='gpu' needs cupy, which is not installed; install a cupy "
                "wheel matching the CUDA runtime on this host, or pass "
                "engine='cpu'"
            )
        return "cpu", "cupy not installed"

    free_bytes = _deshear_gpu.free_device_bytes()
    if free_bytes is None:
        if engine == "gpu":
            raise SnoutyEngineError(
                "engine='gpu' was asked for and no CUDA device visible; cupy "
                "imported, but no device answered. Check the allocation and the "
                "driver, or pass engine='cpu'"
            )
        return "cpu", "no CUDA device visible"

    needed = required_bytes()
    if free_bytes < needed:
        # Capacity, not a broken environment. An explicit engine="gpu" runs
        # this scene on the CPU rather than failing the convert.
        return "cpu", (
            f"device memory {_format_bytes(free_bytes)} below required {_format_bytes(needed)}"
        )

    return "gpu", None
