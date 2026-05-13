#!/usr/bin/env python3
"""
销售记忆引擎 - Embedding Server (常驻内存，避免每次加载模型)
通过 Unix socket 通信，轻量高效
"""

import os
import sys
import json
import socket
import signal
import threading
from pathlib import Path

# 必须使用 venv Python 运行
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from sentence_transformers import SentenceTransformer

SOCKET_PATH = "/tmp/sales-memory-embed.sock"
MODEL_NAME = "thenlper/gte-small"

# 加载模型
print(f"[SERVER] 加载 {MODEL_NAME}...", file=sys.stderr, flush=True)
model = SentenceTransformer(MODEL_NAME)
dim = model.get_embedding_dimension()
print(f"[SERVER] 就绪: {dim}d", file=sys.stderr, flush=True)


def handle_client(conn):
    """处理单次请求: {"texts": ["...", "..."]} -> {"embeddings": [[...], [...]]}"""
    try:
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break

        req = json.loads(data.decode().strip())
        texts = req.get("texts", [])
        normalize = req.get("normalize", True)

        if not texts:
            resp = {"error": "no texts"}
        else:
            embs = model.encode(texts, normalize_embeddings=normalize)
            resp = {
                "embeddings": [e.tolist() for e in embs],
                "dim": dim,
            }

        conn.sendall((json.dumps(resp) + "\n").encode())
    except Exception as e:
        conn.sendall((json.dumps({"error": str(e)}) + "\n").encode())
    finally:
        conn.close()


def main():
    # 清理旧 socket
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(5)
    os.chmod(SOCKET_PATH, 0o666)

    print(f"[SERVER] 监听 {SOCKET_PATH}", file=sys.stderr, flush=True)

    def shutdown(sig, frame):
        print("[SERVER] 关闭中...", file=sys.stderr, flush=True)
        server.close()
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        conn, _ = server.accept()
        t = threading.Thread(target=handle_client, args=(conn,))
        t.daemon = True
        t.start()


if __name__ == "__main__":
    main()
