import glob
import os
import shutil

_ = lambda x: x
from osuawa import C

shutil.rmtree(C.LOGS.value, ignore_errors=True)
shutil.rmtree(C.OUTPUT_DIRECTORY.value, ignore_errors=True)
shutil.rmtree(C.STATIC_DIRECTORY.value, ignore_errors=True)

lck_pattern = glob.glob("./*LCK")
for lck in lck_pattern:
    os.remove(lck)

shutil.rmtree(C.OAUTH_TOKEN_DIRECTORY.value, ignore_errors=True)
shutil.rmtree(C.COMPONENTS_SHELVES_DIRECTORY.value, ignore_errors=True)
if os.path.exists("./osuawa.db"):
    os.remove("./osuawa.db")
