import os
import importlib
import logging

log = logging.getLogger("red.holo-cogs.gptmemory")

module_dir = os.path.dirname(__file__)

for fname in os.listdir(module_dir):
    if fname.endswith('.py') and fname != 'base.py' and not fname.startswith('__'):
        modname = f"{__name__}.{fname[:-3]}"
        log.info(f"Importing {modname}")
        importlib.import_module(modname)
