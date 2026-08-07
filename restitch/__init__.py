"""restitch — set-completion inventory redeployment with a provable transfer plan.

Library surface (the CLI and web console are thin shells over exactly this):

    from restitch import (
        Policy, policy_from_dict, permissive,      # levers, validated
        load_manifest, resolve,                    # files + profiles -> inputs
        run,                                       # the engine
        canonical_moves, plan_json,                # the plan's identity + metrics
        render_workbook, verify_workbook,          # deliverable + independent proof
    )

    rr = resolve(load_manifest("manifest.yaml"), policy_from_dict({...}))
    R = run(rr.inputs, rr.policy)
    battery = render_workbook(R, "movement.xlsx")
    proof = verify_workbook("movement.xlsx", rr.inputs, rr.policy)
"""
from .core.engine import run
from .core.ids import store_id
from .core.policy import Policy, permissive
from .core.policy import from_dict as policy_from_dict
from .core.policy import to_dict as policy_to_dict
from .core.summary import canonical_moves, plan_json
from .core.verify import verify_workbook
from .io.manifest import load_manifest, resolve
from .io.mapping import RoleMapping, load_profile
from .render.workbook import render_workbook

__version__ = "0.1.0"

__all__ = [
    "Policy",
    "RoleMapping",
    "__version__",
    "canonical_moves",
    "load_manifest",
    "load_profile",
    "permissive",
    "plan_json",
    "policy_from_dict",
    "policy_to_dict",
    "render_workbook",
    "resolve",
    "run",
    "store_id",
    "verify_workbook",
]
