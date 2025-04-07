import sys
import asyncio
import platform
import subprocess
import os


def resolve_config_name(default="GSEA"):
    """
    Check sys.argv for an unknown flag (like --GSEA_test).
    If found, return the flag name (without '--'). Otherwise, return the default.
    """
    # Skip the first element (script name)
    for arg in sys.argv[1:]:
        # If the argument starts with '--' and it's not '--config'
        if arg.startswith("--") and arg != "--config":
            # Remove the '--' and return the rest as the config name
            return arg[2:]
    return default


# Determine the configuration name based on command-line arguments.
CONFIG_NAME = resolve_config_name("GSEA")
CONFIG_PATH = f"./configs_system_instruction/{CONFIG_NAME}.json"

print(platform.system())
if platform.system() == "Windows":
    from asyncio import WindowsSelectorEventLoopPolicy

    asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())

print(f"Using config: {CONFIG_NAME}")
print("Running RAG_workflow.py...")
subprocess.run(f"python RAG_workflow.py --config {CONFIG_PATH}", shell=True, check=True)

print("Running gprofiler.py...")
subprocess.run(f"python gprofiler.py --config {CONFIG_PATH}", shell=True, check=True)

print("Running automated_validation.py...")
subprocess.run("python automated_validation.py", shell=True, check=True)
