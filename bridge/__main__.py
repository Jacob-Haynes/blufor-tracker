"""Entry point for `python -m bridge` — runs the legacy simulator.

For production use, run mqtt_shim directly:
    python -m bridge.mqtt_shim --port /dev/ttyUSB0
"""

from bridge.mesh_bridge import main

main()