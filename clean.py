import shutil

_ = lambda x: x
from osuawa import C

shutil.rmtree(C.LOGS.value, ignore_errors=True)
shutil.rmtree(C.UPLOADED_DIRECTORY.value, ignore_errors=True)
