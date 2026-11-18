"""Entry point for `python -m bridge`.

    python -m bridge --simulate      # simulated BFT units on ATAK (bridge.bft_sim, contract v5)
    python -m bridge --legacy ...    # LEGACY dev-only pytak radio bridge (mesh_bridge.py)

Production is `python -m bridge.mqtt_shim` (what mesh-bridge.service runs).
"""

import sys

if "--simulate" in sys.argv[1:] and "--legacy" not in sys.argv[1:]:
    from bridge.bft_sim import main
else:
    from bridge.mesh_bridge import main

main()
