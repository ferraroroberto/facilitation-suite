"""A stand-in OBS: the obs-websocket v5 handshake and the three requests the app uses.

Speaks the real protocol (Hello → Identify → Identified, then Request /
RequestResponse, op codes 0/1/2/6/7) so the real ``obsws-python`` client
talks to it unchanged. No authentication. Records every scene switch.
"""

from __future__ import annotations

import json
import socket
import threading
from typing import Any, Optional

from websockets.sync.server import ServerConnection, serve


class FakeObs:
    def __init__(self, scenes: Optional[list[str]] = None) -> None:
        self.scenes = scenes or ["Slides + camera", "Camera PiP", "Screen only"]
        self.current = self.scenes[0]
        self.switched: list[str] = []
        self.fail_next = False
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = int(s.getsockname()[1])
        self._server = serve(self._handle, "127.0.0.1", self.port)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _handle(self, ws: ServerConnection) -> None:
        ws.send(json.dumps({"op": 0, "d": {"obsWebSocketVersion": "5.6.2", "rpcVersion": 1}}))
        for raw in ws:
            msg = json.loads(raw)
            if msg.get("op") == 1:
                ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
            elif msg.get("op") == 6:
                ws.send(json.dumps({"op": 7, "d": self._respond(msg["d"])}))

    def _respond(self, d: dict[str, Any]) -> dict[str, Any]:
        kind = d["requestType"]
        out: dict[str, Any] = {"requestType": kind, "requestId": d["requestId"], "requestStatus": {"result": True, "code": 100}}
        if self.fail_next:
            self.fail_next = False
            out["requestStatus"] = {"result": False, "code": 600, "comment": "boom"}
            return out
        if kind == "GetVersion":
            out["responseData"] = {"obsVersion": "32.2.2", "obsWebSocketVersion": "5.6.2", "rpcVersion": 1,
                                   "availableRequests": [], "supportedImageFormats": [], "platform": "windows",
                                   "platformDescription": "test"}
        elif kind == "GetSceneList":
            out["responseData"] = {"currentProgramSceneName": self.current, "currentPreviewSceneName": None,
                                   "scenes": [{"sceneIndex": i, "sceneName": n} for i, n in enumerate(self.scenes)]}
        elif kind == "SetCurrentProgramScene":
            name = (d.get("requestData") or {}).get("sceneName")
            if name not in self.scenes:
                out["requestStatus"] = {"result": False, "code": 600, "comment": f"No source was found by the name of `{name}`."}
            else:
                self.current = name
                self.switched.append(name)
        else:
            out["requestStatus"] = {"result": False, "code": 204, "comment": "unknown request"}
        return out

    def close(self) -> None:
        self._server.shutdown()
