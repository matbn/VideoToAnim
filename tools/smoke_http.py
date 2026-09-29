"""Smoke test HTTP: sobe a verificacao do fluxo completo pela API.

Uso:
    python tools/smoke_http.py [base_url] [caminho_do_video]

Faz: GET /api/backends -> POST /api/jobs -> polling -> download GLB + FBX.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    video = sys.argv[2] if len(sys.argv) > 2 else str(Path(__file__).resolve().parent.parent / "storage" / "sample.mp4")
    if not Path(video).exists():
        print(f"video de amostra ausente: {video}")
        return 2

    with httpx.Client(base_url=base, timeout=60.0) as c:
        h = c.get("/api/health").json()
        print("health:", h)

        backends = c.get("/api/backends").json()
        names = [b["name"] for b in backends["backends"]]
        print("default:", backends["default"], "| backends:", names)
        assert backends["default"] == "vitpose", "default deveria ser vitpose"
        required = ("vitpose", "mediapipe")
        forbidden = ("openpose", "simplebaseline", "mhformer")
        for req in required:
            assert req in names, f"backend livre ausente: {req}"
        for bad in forbidden:
            assert bad not in names, f"backend com conflito comercial ainda no dropdown: {bad}"
        print("OK dropdown contem apenas backends livres:", names)

        with open(video, "rb") as fh:
            r = c.post(
                "/api/jobs",
                data={"backend": "synthetic", "params": json.dumps({"fps": 12, "smoothing": "oneeuro"})},
                files={"video": (Path(video).name, fh, "video/mp4")},
            )
        r.raise_for_status()
        job_id = r.json()["job_id"]
        print("job criado:", job_id)

        for _ in range(120):
            job = c.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("done", "error"):
                break
            time.sleep(0.5)
        print("status final:", job["status"])
        if job["status"] != "done":
            print("erro:", job.get("error"))
            print(job.get("log"))
            return 1
        print("metrics:", json.dumps(job["metrics"], ensure_ascii=False))

        glb = c.get(f"/api/jobs/{job_id}/artifacts/glb")
        fbx = c.get(f"/api/jobs/{job_id}/artifacts/fbx")
        print("download GLB:", glb.status_code, len(glb.content), "bytes, magic", glb.content[:4])
        print("download FBX:", fbx.status_code, len(fbx.content), "bytes, header", fbx.content[:22])
        assert glb.status_code == 200 and glb.content[:4] == b"glTF"
        assert fbx.status_code == 200 and fbx.content.startswith(b"; FBX 7.4.0")

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
