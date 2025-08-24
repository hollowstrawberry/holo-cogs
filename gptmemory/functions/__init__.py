import os
import importlib

# import entire gptmemory.functions folder

module_dir = os.path.dirname(__file__)

for fname in os.listdir(module_dir):
    if fname.endswith('.py') and fname != 'base.py' and not fname.startswith('__'):
        modname = f"{__name__}.{fname[:-3]}"
        importlib.import_module(modname)
