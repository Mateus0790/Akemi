"""Servidor MCP de mentira, por stdio, para os testes (uma mensagem JSON por linha)."""

import json
import sys

TOOLS = [
    {"name": "Echo", "description": "Repete o texto.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"texto": {"type": "string", "description": "o que repetir"}},
                     "required": ["texto"]}},
    {"name": "apagar", "description": "Apaga um arquivo.", "annotations": {"destructiveHint": True},
     "inputSchema": {"type": "object", "properties": {"caminho": {"type": "string"}}}},
    {"name": "quebra", "description": "Sempre dá erro."},
    {"name": "morre", "description": "Fecha o servidor."},
]


def send(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    print("servidor falso no ar", file=sys.stderr, flush=True)
    for line in sys.stdin:
        msg = json.loads(line)
        method, rid = msg.get("method"), msg.get("id")
        params = msg.get("params") or {}
        if rid is None:
            continue  # notificação
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "falso", "version": "1"}}})
        elif method == "tools/list":
            page = TOOLS[:2] if not params.get("cursor") else TOOLS[2:]  # duas páginas
            result = {"tools": page}
            if not params.get("cursor"):
                result["nextCursor"] = "p2"
            send({"jsonrpc": "2.0", "id": rid, "result": result})
        elif method == "tools/call":
            name, args = params["name"], params.get("arguments") or {}
            if name == "Echo":
                # O servidor pede um ping antes de responder (o cliente tem que aguentar).
                send({"jsonrpc": "2.0", "id": "srv-1", "method": "ping"})
                pong = json.loads(sys.stdin.readline())
                assert pong["id"] == "srv-1" and pong["result"] == {}
                send({"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "log"}})
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": f"eco: {args.get('texto')}"}]}})
            elif name == "apagar":
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": f"apagado {args.get('caminho')}"}]}})
            elif name == "quebra":
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": "deu ruim"}], "isError": True}})
            elif name == "morre":
                sys.exit(0)
            else:
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "não existe"}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "método?"}})


if __name__ == "__main__":
    main()
