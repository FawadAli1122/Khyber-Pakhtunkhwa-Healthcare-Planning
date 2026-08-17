"""Run with: python -m server
Starts the local dashboard + admin server on 127.0.0.1 only - never
0.0.0.0 - per docs/superpowers/specs/
2026-08-15-backend-admin-panel-phase2-design.md section 3.
"""
import uvicorn

HOST = "127.0.0.1"
PORT = 8420


def main():
    uvicorn.run("server.app:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
