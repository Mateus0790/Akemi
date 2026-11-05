"""Modo console: converse com a Akemi pelo terminal, sem áudio.

    python -m akemi.console            # usa o provider do .env
    python -m akemi.console --echo     # sem API key (respostas de teste)
    python -m akemi.console --voz      # fala em voz alta (Piper)
    python -m akemi.console --mic      # ouve você pelo microfone (diga "Akemi, ...")
    python -m akemi.console --tela     # olha a tela (OCR + modelo de visão opcional)

Comandos:
    /chat <nick> <mensagem>   simula uma mensagem do chat da live
    /olhar                    faz ela olhar a tela agora (precisa de --tela)
    /tela <texto>             simula algo visto na tela
    /audio <texto>            simula algo ouvido no áudio do PC
    /pensar                   força um momento de pensamento
    /estado                   mostra humor, objetivo, tarefas, gostos e opiniões
    /saude                    como ela está por dentro (cérebro em uso, voz, autocura)
    /pausar | /voltar         pausa/retoma a autonomia
    /corpo                    mostra em que corpo ela está e os sentidos dele
    /evolucao                 o que ela mudou em si (e o que está pendente)
    /evoluir                  pede para ela revisar as conversas agora
    /desfazer N               tira uma mudança dela (/aprovar N, /rejeitar N sem modo autônomo)
    /chip <arquivo>           grava a identidade dela num chip (akemi-chip)
    /sair
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from akemi.core.config import Settings
from akemi.core.events import Event
from akemi.core.logging_setup import configure_logging
from akemi.core.orchestrator import Akemi


async def _ainput(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


async def run(echo: bool, voice: bool = False, mic: bool = False, screen: bool = False) -> None:
    settings = Settings()
    if voice:
        if settings.tts_engine == "console":  # TTS_ENGINE do .env escolhe a voz
            settings.tts_engine = "piper"
    if mic:
        settings.mic_enabled = True
    if screen:
        settings.screen_enabled = True
    if echo:
        settings.brain_provider = "echo"
        settings.deep_provider = None
    configure_logging(settings.log_level, settings.log_json)
    akemi = Akemi(settings)
    await akemi.start()
    print("Akemi no console. Digite /sair para encerrar, /ajuda para comandos.\n")
    try:
        while True:
            try:
                line = (await _ainput("você> ")).strip()
            except EOFError:
                break
            if not line:
                continue
            if line in ("/sair", "/exit", "/quit"):
                break
            if line == "/ajuda":
                print(__doc__)
            elif line.startswith("/chat "):
                parts = line.split(maxsplit=2)
                if len(parts) < 3:
                    print("uso: /chat <nick> <mensagem>")
                    continue
                ok = akemi.chat_message(parts[1], parts[2])
                print("  (mensagem entrou no lote)" if ok else "  (mensagem bloqueada/ignorada)")
                akemi.chat.flush()
            elif line == "/olhar":
                if akemi.screen is None:
                    print("  (visão desligada: rode com --tela)")
                    continue
                seen = await akemi.screen.step(force=True)
                print(f"  (viu: {seen})" if seen else "  (nada legível na tela)")
            elif line.startswith("/tela "):
                akemi.observe("screen", line[6:])
            elif line.startswith("/audio "):
                akemi.observe("audio", line[7:])
            elif line == "/pensar":
                data = await akemi.mind.think(Event("tick.idle", source="console"))
                print(f"  (pensamento: {data.get('pensamento', '—')})")
            elif line == "/estado":
                print(akemi.state.describe())
            elif line == "/saude":
                print(akemi.describe_health())
            elif line == "/corpo":
                st = akemi.state
                print(f"  corpo: {st.body or '—'} | sentidos: {', '.join(st.senses) or 'nenhum'}")
            elif line == "/evolucao":
                print(akemi.evolution.describe())
            elif line == "/evoluir":
                made = await akemi.evolve(force=True)
                print("\n".join(p.describe() for p in made) or "  (nada para mudar agora)")
            elif line.split()[0] in ("/aprovar", "/rejeitar", "/desfazer"):
                cmd, _, arg = line.partition(" ")
                arg = arg.strip().lstrip("#")
                if not arg.isdigit():
                    print(f"  uso: {cmd} <número>")
                    continue
                try:
                    print("  " + akemi.decide_evolution(cmd[1:], int(arg)).describe())
                except ValueError as exc:
                    print(f"  (não deu: {exc})")
            elif line.startswith("/chip "):
                from pathlib import Path

                from akemi.storage.chip import ChipError, export_chip

                if akemi.state_path:
                    akemi.state.save(akemi.state_path)
                try:
                    info = export_chip(
                        Path(line[6:].strip()), data_dir=akemi.settings.data_dir,
                        persona_file=akemi.settings.persona_file, owner=akemi.settings.owner_name,
                        key=akemi.settings.chip_key, memory=akemi.memory,
                    )
                    print(info.describe())
                except (ChipError, OSError) as exc:
                    print(f"  (não deu: {exc})")
            elif line == "/pausar":
                akemi.pause.pause("console")
                print("  (pausada: só responde a você)")
            elif line == "/voltar":
                akemi.pause.resume()
                print("  (autonomia retomada)")
            else:
                akemi.owner_say(line)
            await asyncio.sleep(0.1)
    finally:
        await akemi.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Converse com a Akemi pelo terminal")
    parser.add_argument("--echo", action="store_true", help="usa o provider offline de teste")
    parser.add_argument("--voz", action="store_true", help="fala em voz alta (voz do TTS_ENGINE; padrão Piper)")
    parser.add_argument("--mic", action="store_true", help="ouve você pelo microfone")
    parser.add_argument("--tela", action="store_true", help="olha a tela (OCR)")
    parser.add_argument("--listar-mics", action="store_true", help="lista os microfones e sai")
    args = parser.parse_args()
    if args.listar_mics:
        from akemi.audio.mic import MicCapture

        print("\n".join(MicCapture.list_devices()))
        return
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        asyncio.run(run(args.echo, args.voz, args.mic, args.tela))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
