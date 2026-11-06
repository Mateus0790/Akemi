# O hook padrão procura o pacote "webrtcvad", mas a Akemi usa o
# "webrtcvad-wheels" (mesmo módulo, outro nome no pip) e o build quebrava.
from PyInstaller.utils.hooks import copy_metadata

datas = []
for dist in ("webrtcvad-wheels", "webrtcvad"):
    try:
        datas = copy_metadata(dist)
        break
    except Exception:
        continue
