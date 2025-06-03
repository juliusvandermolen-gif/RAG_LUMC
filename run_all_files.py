#!/usr/bin/env python3
import os
import sys
import platform
import runpy
import multiprocessing as mp

mp.set_start_method("spawn", force=True)

os.environ["TOKENIZERS_PARALLELISM"] = "false"


# Helper to pick up the config flag
def resolve_config_name(default="GSEA"):
    for arg in sys.argv[1:]:
        if arg.startswith("--") and arg != "--config":
            return arg[2:]
    return default


CONFIG_NAME = resolve_config_name("GSEA")
CONFIG_PATH = f"./configs_system_instruction/{CONFIG_NAME}.json"

print(platform.system())
if platform.system() == "Windows":
    import asyncio
    from asyncio import WindowsSelectorEventLoopPolicy
    asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())

print(f"Using config: {CONFIG_NAME}")


def run_module_main(module_name, argv):
    """Temporarily swap in sys.argv, then run module.__main__."""
    old_argv = sys.argv
    sys.argv = [module_name] + argv
    try:
        runpy.run_module(module_name, run_name="__main__", alter_sys=False)
    finally:
        sys.argv = old_argv


print("Running RAG_workflow.py…")
run_module_main("RAG_workflow", ["--config", CONFIG_PATH])

print("Running gprofiler.py…")
run_module_main("gprofiler",   ["--config", CONFIG_PATH])

print("Running automated_validation.py…")
run_module_main("automated_validation", [])

print("All done.")
