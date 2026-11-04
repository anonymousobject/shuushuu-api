"""Small helpers for the .npz artifacts the probe writes and reads."""

from pathlib import Path


def npz_path(path: Path) -> Path:
    """The path numpy will actually use: ``np.savez`` appends ``.npz`` when it is missing."""
    path = Path(path)
    return path if path.suffix == ".npz" else path.with_name(path.name + ".npz")
