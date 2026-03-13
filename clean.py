import shutil

_ = lambda x: x
from osuawa import C

shutil.rmtree(C.LOGS.value)
shutil.rmtree(C.UPLOADED_DIRECTORY.value)
