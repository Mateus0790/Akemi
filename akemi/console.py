"""Modo console: converse com a Akemi pelo terminal, sem áudio.

    python -m akemi.console            # usa o provider do .env
    python -m akemi.console --echo     # sem API key (respostas de teste)
    python -m akemi.console --voz      # fala em voz alta (Fish Audio; sem chave, Piper)
    python -m akemi.console --mic      # ouve você pelo microfone (diga "Akemi, ...")
    python -m akemi.console --tela     # olha a tela (OCR + modelo de visão opcional)
    python -m akemi.console --celular  # deixa o app do celular conectar (precisa de AKEMI_TOKEN)
    python -m akemi.console --cadastrar-voz   # ela aprende a reconhecer a sua voz
    python -m akemi.console --casca claude    # liga o Modo Casca (claude|codex|ambos|desligar)

Comandos:
    /chat <nick> <mensagem>   simula uma mensagem do chat da live
    /olhar                    faz ela olhar a tela agora (precisa de --tela)
    /tela <texto>             simula algo visto na tela
    /audio <texto>            simula algo ouvido no áudio do PC
    /pensar                   força um momento de pensamento
    /estado                   mostra humor, objetivo, tarefas, gostos e opiniões
    /saude                    como ela está por dentro (cérebro em uso, voz, autocura)
    /mcp                      os apps conectados por MCP e as ferramentas de cada um
    /pausar | /voltar         pausa/retoma a autonomia
    /corpo                    mostra em que corpo ela está e os sentidos dele
    /voz                      reconhecimento da sua voz: cadastro, limites, quem ela ouviu
    /pr <pedido>              ela muda o próprio código e abre um PR (OFICINA_ENABLED)
    /prs                      os PRs que ela abriu ou tentou abrir
    /evolucao                 o que ela mudou em si (e o que está pendente)
    /evoluir                  pede para ela revisar as conversas agora
    /desfazer N               tira uma mudança dela (/aprovar N, /rejeitar N sem modo autônomo)
    /chip <arquivo>           grava a identidade dela num chip (akemi-chip)
    /casca                    o que o Claude Code/Codex estão fazendo (Modo Casca)
    /lembretes                os lembretes pendentes
    /rotinas                  suas rotinas de voz ("quando eu disser X, faz Y")
    /jogar <jogo>             velha, forca, pedra ou adivinhacao (ou diga "vamos jogar forca")
    /chamada                  entra ou sai da call de voz (ou diga "Akemi, entra na call")
    /vigias                   o que ela está vigiando na tela
    /sair
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from akemi.core.config import Settings
from akemi.core.events import Event
from akemi.core.logging_setup import configure_logging
from akemi.core.orchestrator import Akemi, default_voice
from akemi.games.arcade import which_game


async def _ainput(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


def lan_address() -> str:
    """O IP deste PC na rede de casa (para digitar no app do celular)."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # UDP: não manda nada, só escolhe a interface
            return s.getsockname()[0]
    except OSError:
        return "IP-DESTE-PC"


async def start_phone(akemi: Akemi):
    """Abre a porta para o app do celular. Devolve o servidor (ou None se não deu)."""
    from akemi.net.companion import CompanionHub
    from akemi.net.server import MIN_TOKEN_LEN

    s = akemi.settings
    if len(s.akemi_token) < MIN_TOKEN_LEN:
        print(
            "  (celular desligado: defina AKEMI_TOKEN com pelo menos 16 caracteres no .env. Gere com:\n"
            '   python -c "import secrets; print(secrets.token_urlsafe(24))")'
        )
        return None
    hub = CompanionHub(akemi, s.akemi_token)
    hub.install()
    akemi.phone = hub
    try:
        ws = await hub.serve(s.server_host, s.server_port)
    except OSError as exc:
        print(f"  (celular desligado: não consegui abrir a porta {s.server_port}: {exc})")
        return None
    print(f"  📱 App do celular: servidor ws://{lan_address()}:{s.server_port} (mesmo Wi-Fi, mesmo AKEMI_TOKEN)")
    return ws


async def run(
    echo: bool, voice: bool = False, mic: bool = False, screen: bool = False, phone: bool = False
) -> None:
    settings = Settings()
    if voice:
        if settings.tts_engine == "console":  # TTS_ENGINE do .env escolhe a voz
            settings.tts_engine = default_voice(settings)
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
    akemi.bus.subscribe("jogo.tabuleiro", lambda e: print("\n" + e.text + "\n"))
    phone_ws = await start_phone(akemi) if phone else None
    if settings.avatar_enabled and akemi.avatar is None:
        print("  (avatar desligado: precisa do tkinter e de uma tela; veja docs/CAPRI.md)")
    if akemi.casca_listener is not None:
        print(f"  🤖 Modo Casca: acompanhando {', '.join(sorted(akemi.casca.sources))}")
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
            elif line == "/mcp":
                print(akemi.describe_mcp())
            elif line.startswith("/pr ") or line == "/prs":
                if akemi.oficina is None:
                    print("  (oficina desligada: veja docs/OFICINA.md)")
                elif line == "/prs":
                    print("\n".join("  " + r.describe() for r in akemi.oficina.history()[-10:]) or "  (nenhum ainda)")
                else:
                    print("  (trabalhando... isso leva uns minutos; ela avisa quando terminar)")
                    akemi._tasks.append(asyncio.create_task(akemi.open_pr(line[4:])))
            elif line == "/voz":
                vid = akemi.senses.voice_id
                if vid is not None:
                    print(vid.describe())
                elif not akemi.settings.mic_enabled:
                    print("  (microfone desligado: rode com --mic)")
                else:
                    print("  voz não cadastrada: tudo no microfone vale como você. Use akemi --cadastrar-voz")
            elif line == "/corpo":
                st = akemi.state
                print(f"  corpo: {st.body or '—'} | sentidos: {', '.join(st.senses) or 'nenhum'}")
                if akemi.phone is not None:
                    ph = akemi.phone.status()
                    print(f"  celular: {ph['nome'] or 'desconectado'}{' (respondendo nele)' if ph['em_foco'] else ''}")
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
            elif line == "/casca":
                if akemi.casca is None:
                    print("  (Modo Casca desligado: akemi --casca claude|codex|ambos)")
                else:
                    print(akemi.casca.describe())
            elif line == "/lembretes":
                items = akemi.reminders.pending
                print("\n".join(f"  #{r.id} {r.describe()}" for r in items) or "  (nenhum lembrete)")
            elif line.startswith("/jogar"):
                name = line[6:].strip() or "velha"
                try:
                    akemi.bus.publish(Event("speech.say", {"text": akemi.arcade.start(
                        which_game(name) or name)}, source="jogo"))
                except ValueError as exc:
                    print(f"  ({exc})")
            elif line == "/chamada":
                said = akemi.call.stop() if akemi.call.active else akemi.call.start()
                akemi.bus.publish(Event("speech.say", {"text": said}, source="chamada"))
            elif line == "/rotinas":
                print("  " + akemi.routines.describe())
            elif line == "/vigias":
                print("  " + akemi.lookout.describe())
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
        if phone_ws is not None:
            phone_ws.close()
        await akemi.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Converse com a Akemi pelo terminal")
    parser.add_argument("--echo", action="store_true", help="usa o provider offline de teste")
    parser.add_argument("--voz", action="store_true", help="fala em voz alta (voz do TTS_ENGINE; padrão Fish Audio com FISH_API_KEY, senão Piper)")
    parser.add_argument("--mic", action="store_true", help="ouve você pelo microfone")
    parser.add_argument("--tela", action="store_true", help="olha a tela (OCR)")
    parser.add_argument("--celular", action="store_true", help="deixa o app do celular conectar")
    parser.add_argument("--cadastrar-voz", action="store_true", help="ela aprende a reconhecer a sua voz")
    parser.add_argument("--esquecer-voz", action="store_true", help="apaga o cadastro da sua voz")
    parser.add_argument("--listar-mics", action="store_true", help="lista os microfones e sai")
    parser.add_argument("--casca", choices=["claude", "codex", "ambos", "desligar"],
                        help="liga o Modo Casca: ela vira o corpo do Claude Code/Codex (e sai)")
    args = parser.parse_args()
    if args.casca:
        from akemi.casca.install import HookError, configure, save_mode

        settings = Settings()
        try:
            for msg in configure(args.casca):
                print(msg)
        except HookError as exc:
            raise SystemExit(f"Não deu: {exc}") from exc
        save_mode(settings.data_dir, args.casca)
        if args.casca == "desligar":
            print("Modo Casca desligado.")
        else:
            print("Pronto. Agora é só abrir a Akemi (akemi) e usar o assistente normalmente.")
        return
    if args.cadastrar_voz or args.esquecer_voz:
        from akemi.audio.voice_id import VOICE_FILE

        settings = Settings()
        if args.esquecer_voz:
            path = settings.data_dir / VOICE_FILE
            if path.exists():
                path.unlink()
                print(f"Cadastro da voz apagado ({path}). O microfone volta a valer como você.")
            else:
                print("Não tinha voz cadastrada.")
            return
        from akemi.audio.enroll import run_enrollment

        raise SystemExit(asyncio.run(run_enrollment(
            settings.data_dir, settings.voice_model_path, settings.mic_device, settings.vad_aggressiveness,
        )))
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
        asyncio.run(run(args.echo, args.voz, args.mic, args.tela, args.celular))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
