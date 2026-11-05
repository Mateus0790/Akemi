"""Ajusta o projeto Android gerado pelo `flutter create`.

As notificações (flutter_local_notifications) precisam do "desugaring" de
Java. O `flutter create` não liga isso, então este script liga, tanto no
build.gradle.kts (Flutter novo) quanto no build.gradle (antigo). Pode rodar
mais de uma vez.

    cd app && flutter create --platforms=android --org com.mateus0790 . && python tool/prepare_android.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DESUGAR = "com.android.tools:desugar_jdk_libs:2.1.4"


def patch(path: Path) -> None:
    s = path.read_text(encoding="utf-8")
    kts = path.suffix == ".kts"
    if "desugar_jdk_libs" in s:
        print(f"{path}: já ajustado")
        return
    flag = "isCoreLibraryDesugaringEnabled = true" if kts else "coreLibraryDesugaringEnabled true"
    s, n = re.subn(r"compileOptions\s*\{", lambda m: m.group(0) + f"\n        {flag}", s, count=1)
    if n == 0:
        s, n = re.subn(r"android\s*\{", lambda m: m.group(0) + f"\n    compileOptions {{\n        {flag}\n    }}", s, count=1)
    if n == 0:
        sys.exit(f"{path}: não achei o bloco android {{ }}")
    dep = f'coreLibraryDesugaring("{DESUGAR}")' if kts else f"coreLibraryDesugaring '{DESUGAR}'"
    s = s.rstrip() + f"\n\ndependencies {{\n    {dep}\n}}\n"
    path.write_text(s, encoding="utf-8")
    print(f"{path}: desugaring ligado")


def main() -> None:
    app = Path(__file__).resolve().parent.parent / "android" / "app"
    for name in ("build.gradle.kts", "build.gradle"):
        if (app / name).exists():
            patch(app / name)
            return
    sys.exit("rode `flutter create --platforms=android .` antes")


if __name__ == "__main__":
    main()
