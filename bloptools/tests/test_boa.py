import numpy as np
from sirepo_bluesky.sirepo_ophyd import create_classes

from bloptools.bo import BayesianOptimizationAgent
from bloptools.experiments.nsls2 import tes_shadow


def test_shadow_bo_optimizer(RE, db, shadow_tes_simulation):
    data, schema = shadow_tes_simulation.auth("shadow", "00000002")
    classes, objects = create_classes(shadow_tes_simulation.data, connection=shadow_tes_simulation)
    globals().update(**objects)

    data["models"]["simulation"]["npoint"] = 100000
    data["models"]["watchpointReport12"]["histogramBins"] = 32

    kbs = [kbv.x_rot, kbv.offz]
    kb_bounds = np.array([[-0.10, +0.10], [-0.50, +0.50]])

    for dof in dofs:
        dof.kind = "hinted"

    boa = BayesianOptimizationAgent(dofs=kbs, dets=[w9], dof_bounds=kb_bounds, db=db, experiment=tes_shadow)

    RE(boa.initialize(init_scheme="quasi-random", n_init=8))

    boa.plot_state(gridded=True)