import sys
import asyncio
import platform
import subprocess

print(platform.system())
if platform.system() == "Windows":
    print("Setting settings:")
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

print("Starting RAG_workflow.py and gprofiler.ipynb in parallel...")

p1 = subprocess.Popen("python RAG_workflow.py", shell=True)
p2 = subprocess.Popen("jupyter nbconvert --execute --to notebook --inplace gprofiler.ipynb", shell=True)

p1.wait()
p2.wait()

print("Both RAG_workflow.py and gprofiler.ipynb have finished. Now running automated_validation.py...")
subprocess.run("python automated_validation.py", shell=True, check=True)
