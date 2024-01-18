import time as ttime
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Tuple

import numpy as np
import pandas as pd
from ophyd import Signal, SignalRO

DOF_FIELD_TYPES = {
    "description": "object",
    "readback": "float",
    "search_bounds": "object",
    "trust_bounds": "object",
    "units": "object",
    "active": "bool",
    "read_only": "bool",
    "log": "bool",
    "tags": "object",
}


class ReadOnlyError(Exception):
    ...


def _validate_dofs(dofs):
    dof_names = [dof.name for dof in dofs]

    # check that dof names are unique
    unique_dof_names, counts = np.unique(dof_names, return_counts=True)
    duplicate_dof_names = unique_dof_names[counts > 1]
    if len(duplicate_dof_names) > 0:
        raise ValueError(f"Duplicate name(s) in supplied dofs: {duplicate_dof_names}")

    return list(dofs)


@dataclass
class DOF:
    """A degree of freedom (DOF), to be used by an agent.

    Parameters
    ----------
    name: str
        The name of the DOF. This is used as a key to index observed data.
    description: str, optional
        A longer name for the DOF.
    units: str
        The units of the DOF (e.g. mm or deg). This is just for plotting and general sanity checking.
    search_bounds: tuple
        A tuple of the lower and upper limit of the DOF for the agent to search.
    trust_bounds: tuple, optional
        The agent will reject all data where the DOF value is outside the trust bounds.
    read_only: bool
        If True, the agent will not try to set the DOF. Must be set to True if the supplied ophyd
        device is read-only.
    active: bool
        If True, the agent will try to use the DOF in its optimization. If False, the agent will
        still read the DOF but not include it any model or acquisition function.
    log: bool
        Whether to apply a log to the objective, i.e. to make the process outputs more Gaussian.
    tags: list
        A list of tags. These make it easier to subset large groups of dofs.
    device: Signal, optional
        An ophyd device. If not supplied, a dummy ophyd device will be generated.
    """

    name: str = None
    description: str = ""
    search_bounds: Tuple[float, float] = None
    trust_bounds: Tuple[float, float] = None
    units: str = ""
    read_only: bool = False
    active: bool = True
    log: bool = False
    tags: list = field(default_factory=list)
    device: Signal = None

    # Some post-processing. This is specific to dataclasses
    def __post_init__(self):
        if self.trust_bounds is not None:
            self.trust_bounds = tuple(self.trust_bounds)
        if self.search_bounds is None:
            if not self.read_only:
                raise ValueError("You must specify search_bounds if the device is not read-only.")
        else:
            self.search_bounds = tuple(self.search_bounds)

        self.uuid = str(uuid.uuid4())

        if self.name is None:
            self.name = self.device.name if hasattr(self.device, "name") else self.uuid

        if self.device is None:
            self.device = Signal(name=self.name)

        if not self.read_only:
            # check that the device has a put method
            if isinstance(self.device, SignalRO):
                raise ValueError("You must specify read_only=True for a read-only device.")

        if self.log:
            if not self.search_lower_bound > 0:
                raise ValueError("Search bounds must be positive if log=True.")

        # all dof degrees of freedom are hinted
        self.device.kind = "hinted"

    @property
    def search_lower_bound(self):
        if self.read_only:
            raise ValueError("Read-only DOFs do not have search bounds.")
        return float(self.summary.search_bounds[0])

    @property
    def search_upper_bound(self):
        if self.read_only:
            raise ValueError("Read-only DOFs do not have search bounds.")
        return float(self.summary.search_bounds[1])

    @property
    def trust_lower_bound(self):
        return float(self.summary.trust_bounds[0])

    @property
    def trust_upper_bound(self):
        return float(self.summary.trust_bounds[1])

    @property
    def readback(self):
        return self.device.read()[self.device.name]["value"]

    @property
    def summary(self) -> pd.Series:
        series = pd.Series(index=list(DOF_FIELD_TYPES.keys()), dtype="object")
        for attr in series.index:
            value = getattr(self, attr)
            if attr == "trust_bounds":
                if value is None:
                    value = (0, np.inf) if self.log else (-np.inf, np.inf)
            series[attr] = value
        return series

    @property
    def label(self) -> str:
        return f"{self.description}{f' [{self.units}]' if len(self.units) > 0 else ''}"

    @property
    def has_model(self):
        return hasattr(self, "model")


class DOFList(Sequence):
    def __init__(self, dofs: list = []):
        _validate_dofs(dofs)
        self.dofs = dofs

    def __getattr__(self, attr):
        if attr in self.names:
            return self.__getitem__(attr)

        raise AttributeError(f"DOFList object has no attribute named '{attr}'.")

    def __getitem__(self, index):
        if type(index) is int:
            return self.dofs[index]
        elif type(index) is str:
            if index not in self.names:
                raise ValueError(f"DOFList has no DOF named {index}.")
            return self.dofs[self.names.index(index)]
        else:
            raise ValueError(f"Invalid index {index}. A DOFList must be indexed by either an integer or a string.")

    def __len__(self):
        return len(self.dofs)

    def __repr__(self):
        return self.summary.__repr__()

    def __repr_html__(self):
        return self.summary.__repr_html__()

    @property
    def summary(self) -> pd.DataFrame:
        table = pd.DataFrame(columns=list(DOF_FIELD_TYPES.keys()), index=self.names)

        for dof in self.dofs:
            for attr, value in dof.summary.items():
                table.at[dof.name, attr] = value

        for attr, dtype in DOF_FIELD_TYPES.items():
            table[attr] = table[attr].astype(dtype)

        return table

    @property
    def names(self) -> list:
        return [dof.name for dof in self.dofs]

    @property
    def devices(self) -> list:
        return [dof.device for dof in self.dofs]

    @property
    def device_names(self) -> list:
        return [dof.device.name for dof in self.dofs]

    @property
    def search_lower_bounds(self) -> np.array:
        return np.array([dof.search_lower_bound if not dof.read_only else dof.readback for dof in self.dofs])

    @property
    def search_upper_bounds(self) -> np.array:
        return np.array([dof.search_upper_bound if not dof.read_only else dof.readback for dof in self.dofs])

    @property
    def search_bounds(self) -> np.array:
        """
        Returns a (n_dof, 2) array of bounds.
        """
        return np.c_[self.search_lower_bounds, self.search_upper_bounds]

    @property
    def trust_lower_bounds(self) -> np.array:
        return np.array([dof.trust_lower_bound for dof in self.dofs])

    @property
    def trust_upper_bounds(self) -> np.array:
        return np.array([dof.trust_upper_bound for dof in self.dofs])

    @property
    def trust_bounds(self) -> np.array:
        """
        Returns a (n_dof, 2) array of bounds.
        """
        return np.c_[self.trust_lower_bounds, self.trust_upper_bounds]

    @property
    def readback(self) -> np.array:
        return np.array([dof.readback for dof in self.dofs])

    @property
    def active(self):
        return np.array([dof.active for dof in self.dofs])

    @property
    def read_only(self):
        return np.array([dof.read_only for dof in self.dofs])

    def add(self, dof):
        _validate_dofs([*self.dofs, dof])
        self.dofs.append(dof)

    def _dof_active_mask(self, active=None):
        return [_active == active if active is not None else True for _active in self.active]

    def _dof_read_only_mask(self, read_only=None):
        return [_read_only == read_only if read_only is not None else True for _read_only in self.read_only]

    def _dof_tags_mask(self, tags=[]):
        return [np.isin(dof["tags"], tags).any() if tags else True for dof in self.dofs]

    def _dof_mask(self, active=None, read_only=None, tags=[]):
        return [
            (k and m and t)
            for k, m, t in zip(self._dof_read_only_mask(read_only), self._dof_active_mask(active), self._dof_tags_mask(tags))
        ]

    def subset(self, active=None, read_only=None, tags=[]):
        return DOFList([dof for dof, m in zip(self.dofs, self._dof_mask(active, read_only, tags)) if m])

    def activate(self, read_only=None, active=None, tags=[]):
        for dof in self._subset_dofs(read_only, active, tags):
            dof.active = True

    def deactivate(self, read_only=None, active=None, tags=[]):
        for dof in self._subset_dofs(read_only, active, tags):
            dof.active = False


class BrownianMotion(SignalRO):
    """
    Read-only degree of freedom simulating brownian motion
    """

    def __init__(self, name=None, theta=0.95, *args, **kwargs):
        name = name if name is not None else str(uuid.uuid4())

        super().__init__(name=name, *args, **kwargs)

        self.theta = theta
        self.old_t = ttime.monotonic()
        self.old_y = 0.0

    def get(self):
        new_t = ttime.monotonic()
        alpha = self.theta ** (new_t - self.old_t)
        new_y = alpha * self.old_y + np.sqrt(1 - alpha**2) * np.random.standard_normal()

        self.old_t = new_t
        self.old_y = new_y
        return new_y


class TimeReadback(SignalRO):
    """
    Returns the current timestamp.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def get(self):
        return ttime.time()


class ConstantReadback(SignalRO):
    """
    Returns a constant every time you read it (more useful than you'd think).
    """

    def __init__(self, constant=1, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.constant = constant

    def get(self):
        return self.constant