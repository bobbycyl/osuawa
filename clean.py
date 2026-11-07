import shutil

_ = lambda x: x
from osuawa import C

if __name__ == "__main__":
    shutil.rmtree(C.LOGS.value, ignore_errors=True)
    shutil.rmtree(C.UPLOADED_DIRECTORY.value, ignore_errors=True)
